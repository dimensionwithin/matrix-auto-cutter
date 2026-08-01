#pragma once

#include "matrix_auto_cutter/journal_producer.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>

namespace matrix_auto_cutter::obs_adapter {

inline constexpr std::size_t max_recording_path_utf8 = 32'767;
inline constexpr auto calibration_interval = std::chrono::seconds(2);

enum class FrontendEvent {
    recording_starting,
    recording_started,
    recording_stopping,
    recording_stopped,
    other,
};
enum class OutputEvent { started, paused, resumed, stopped };
enum class CallbackKind { frontend, tick, output_start, output_pause, output_resume, output_stop };
enum class LogLevel { info, warning, error };
enum class AdapterState { unloaded, idle, start_pending, active, paused, stopping, failed, unloading };

struct BoundedPath final {
    std::array<char, max_recording_path_utf8 + 1> bytes{};
    std::size_t size{};

    [[nodiscard]] bool assign(std::string_view value) noexcept;
    [[nodiscard]] std::string_view view() const noexcept;
};

struct RecordingSignal final {
    BoundedPath path;
    BoundedPath output_id;
    std::uint64_t absolute_monotonic_ns{};
    std::uint64_t output_frame_count{};
    bool fragmented_mp4{};
};

using FrontendCallback = void (*)(FrontendEvent, void*) noexcept;
using TickCallback = void (*)(void*) noexcept;
using OutputCallback = void (*)(OutputEvent, int, void*) noexcept;

class AdapterHost {
  public:
    virtual ~AdapterHost() = default;
    virtual bool install_callbacks(
        FrontendCallback frontend,
        TickCallback tick,
        void* private_data) noexcept = 0;
    virtual void remove_callbacks() noexcept = 0;
    virtual bool acquire_recording_output() noexcept = 0;
    virtual bool connect_recording_output_signals(
        OutputCallback output,
        void* private_data) noexcept = 0;
    virtual void disconnect_recording_output_signals() noexcept = 0;
    virtual bool capture_recording_output(RecordingSignal& signal) noexcept = 0;
    virtual bool capture_clock(
        std::uint64_t& absolute_monotonic_ns,
        std::uint64_t& output_frame_count) noexcept = 0;
    virtual void release_recording_output() noexcept = 0;
    virtual std::string_view obs_version() const noexcept = 0;
    virtual void log(LogLevel level, std::string_view message) noexcept = 0;
};

class ProducerPort {
  public:
    virtual ~ProducerPort() = default;
    virtual ProducerResult start_recording(const RecordingStart& start) noexcept = 0;
    virtual CallbackResult submit(JournalSnapshot snapshot) noexcept = 0;
    virtual ProducerResult normal_stop(const RecordingStop& stop) noexcept = 0;
    virtual ProducerResult shutdown() noexcept = 0;
    virtual ProducerResult result() const noexcept = 0;
    virtual std::string recording_session_id() const noexcept = 0;
};

class ProducerFactory {
  public:
    virtual ~ProducerFactory() = default;
    virtual std::unique_ptr<ProducerPort> create() noexcept = 0;
};

class WorkerThreadLifetime {
  public:
    virtual ~WorkerThreadLifetime() = default;
    // Production uses a non-returning FreeLibraryAndExitThread implementation.
    virtual void exit_thread() noexcept = 0;
};

class CallbackRegistrationLifetime {
  public:
    virtual ~CallbackRegistrationLifetime() = default;
};

struct AdapterOptions final {
    std::string producer_version{"0.1.0-experimental"};
    UuidFactory session_uuid_factory{uuid_v4};
    UuidFactory event_uuid_factory{uuid_v4};
    std::function<std::optional<std::filesystem::path>()> local_app_data_provider;
    std::function<std::unique_ptr<WorkerThreadLifetime>()> worker_lifetime_factory;
    std::function<std::unique_ptr<CallbackRegistrationLifetime>()>
        callback_lifetime_factory;
    std::function<void(CallbackKind)> callback_probe;
    std::chrono::milliseconds unload_deadline{
        std::chrono::duration_cast<std::chrono::milliseconds>(
            producer_shutdown_deadline + std::chrono::seconds(2))};
};

struct RunReport final {
    ProducerResult result{ProducerResult::producer_internal_error};
    std::string journal_path_utf8;
    std::string recording_session_id;
    std::uint64_t final_frame_count{};
    std::uint64_t calibration_count{};
    bool successful{};
};

class ObsJournalAdapter final {
  public:
    ObsJournalAdapter(AdapterHost& host, ProducerFactory& factory, AdapterOptions options = {});
    ~ObsJournalAdapter();

    ObsJournalAdapter(const ObsJournalAdapter&) = delete;
    ObsJournalAdapter& operator=(const ObsJournalAdapter&) = delete;

    [[nodiscard]] bool load() noexcept;
    void unload() noexcept;
    [[nodiscard]] AdapterState state() const noexcept;
    [[nodiscard]] std::optional<RunReport> last_report() const;

    static void frontend_boundary(FrontendEvent event, void* private_data) noexcept;
    static void tick_boundary(void* private_data) noexcept;
    static void output_boundary(OutputEvent event, int code, void* private_data) noexcept;

  private:
    struct ClockCommand final {
        std::uint64_t absolute_monotonic_ns{};
        std::uint64_t output_frame_count{};
        bool recording_start_anchor{};
    };
    enum class ControlKind { start, pause, resume, stop };
    struct ControlCommand final {
        ControlKind kind{ControlKind::start};
        RecordingSignal signal;
        std::uint64_t absolute_monotonic_ns{};
        std::uint64_t output_frame_count{};
        int code{};
        bool captured{};
    };

    [[nodiscard]] bool enter_callback() noexcept;
    void leave_callback(bool bound_access) noexcept;
    void close_callback_gate() noexcept;
    void probe_callback(CallbackKind kind);
    void on_frontend(FrontendEvent event) noexcept;
    void on_tick() noexcept;
    void on_output(OutputEvent event, int code) noexcept;
    void worker_main() noexcept;
    void process_start(const RecordingSignal& signal) noexcept;
    void process_clock(const ClockCommand& command) noexcept;
    void process_pause_or_resume(const ControlCommand& command) noexcept;
    void process_stop(const ControlCommand& command) noexcept;
    void fail_current_run(ProducerResult result, std::string_view reason) noexcept;
    void force_cleanup(ProducerResult result, std::string_view reason) noexcept;
    void disconnect_output_signals() noexcept;
    void release_output_reference() noexcept;
    void wait_for_callbacks_to_drain() noexcept;
    void wait_for_bound_callbacks_to_drain() noexcept;
    void reset_run() noexcept;
    [[nodiscard]] bool queue_forced_failure() noexcept;

    static constexpr std::uint64_t callback_gate_closed = UINT64_C(1) << 63U;
    static constexpr std::uint64_t callback_gate_count_mask = callback_gate_closed - 1U;

    AdapterHost& host_;
    ProducerFactory& factory_;
    AdapterOptions options_;
    std::atomic<AdapterState> state_{AdapterState::unloaded};
    std::atomic<bool> accepting_snapshots_{};
    std::atomic<std::uint64_t> origin_ns_{};
    std::atomic<std::uint64_t> initial_frame_count_{};
    std::atomic<std::uint64_t> recording_started_ns_{};
    std::atomic<std::uint64_t> recording_started_frame_count_{};
    std::atomic<std::uint64_t> next_calibration_ns_{};
    std::atomic<std::uint64_t> calibration_count_{};
    std::atomic<bool> recording_started_claimed_{};
    std::atomic<bool> recording_started_accepted_{};
    // Tracks actual output-signal order independently of worker scheduling.
    // 0 active, 1 pause queued/paused, 2 resume queued.
    std::atomic<unsigned> observed_pause_state_{};
    std::atomic<std::uint64_t> callback_gate_{callback_gate_closed};
    std::atomic<unsigned> bound_callbacks_in_flight_{};
    mutable std::mutex callback_wait_mutex_;
    std::condition_variable callback_finished_;

    mutable std::mutex command_mutex_;
    std::condition_variable command_changed_;
    static constexpr std::size_t control_command_capacity = 4;
    std::array<ControlCommand, control_command_capacity> control_commands_{};
    std::size_t control_read_{};
    std::size_t control_write_{};
    std::size_t control_size_{};
    std::optional<ClockCommand> pending_clock_;
    std::atomic<bool> forced_shutdown_{};
    bool unload_requested_{};

    mutable std::mutex report_mutex_;
    std::optional<RunReport> last_report_;
    std::unique_ptr<ProducerPort> producer_;
    std::optional<EventSnapshot> pending_recording_started_;
    std::string recording_path_;
    std::filesystem::path journal_path_;
    std::thread worker_;
    std::mutex worker_done_mutex_;
    std::condition_variable worker_done_changed_;
    bool worker_done_{};
    std::atomic<bool> output_reference_held_{};
    std::atomic<bool> output_signals_connected_{};
    std::atomic<bool> loaded_{};
    std::atomic<bool> permanently_unloaded_{};
    std::unique_ptr<CallbackRegistrationLifetime> callback_lifetime_;
};

}  // namespace matrix_auto_cutter::obs_adapter
