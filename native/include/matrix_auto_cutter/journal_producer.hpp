#pragma once

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
#include <variant>

namespace matrix_auto_cutter {

inline constexpr std::size_t default_queue_capacity = 8192;
inline constexpr auto producer_shutdown_deadline = std::chrono::seconds(5);

enum class CallbackResult {
    accepted,
    full,
    terminal,
    internal_error,
};

enum class ProducerResult {
    producer_ok,
    producer_rejected_after_stop,
    producer_failed_queue_overflow,
    producer_failed_io,
    producer_shutdown_timeout,
    producer_internal_error,
};

enum class ProducerState {
    not_started,
    recording_active,
    stop_requested,
    producer_failed_queue_overflow,
    producer_failed_io,
    producer_failed_internal,
    producer_failed_shutdown_timeout,
    stopped_unfinalized,
    closed,
};

enum class EventType {
    recording_started,
    scene_changed,
    intro_started,
    intro_ended,
    outro_started,
    outro_ended,
    stinger_started,
    stinger_ended,
    manual_protection,
};

struct ClockSnapshot final {
    std::uint64_t monotonic_ns{};
    std::uint64_t output_frame_count{};
    bool recording_paused{};
};

struct EventSnapshot final {
    std::string event_id;
    EventType event_type{EventType::recording_started};
    ClockSnapshot clock;
    std::optional<std::string> source_uuid;
    std::optional<std::string> pair_id;
    std::optional<std::string> label;
};

struct CalibrationSnapshot final {
    ClockSnapshot clock;
};

// These are value snapshots.  The adapter creates their UUIDs on its worker,
// never in an OBS callback, and the producer assigns the sequence on its
// writer thread.
struct PauseSnapshot final {
    std::string event_id;
    ClockSnapshot clock;
};

struct ResumeSnapshot final {
    std::string event_id;
    ClockSnapshot clock;
};

using JournalSnapshot =
    std::variant<EventSnapshot, CalibrationSnapshot, PauseSnapshot, ResumeSnapshot>;

struct RecordingStart final {
    std::filesystem::path journal_path;
    std::string recording_path_utf8;
    std::string producer_version;
    std::string obs_version;
    std::optional<std::string> recording_session_id;
};

struct RecordingStop final {
    ClockSnapshot clock;
    std::string recording_path_utf8;
};

class JournalSink {
  public:
    virtual ~JournalSink() = default;
    virtual bool write_line(std::string_view canonical_json_without_lf) noexcept = 0;
};

using JournalSinkFactory =
    std::function<std::unique_ptr<JournalSink>(const std::filesystem::path&)>;
using UuidFactory = std::function<std::string()>;
using WriterThreadExit = std::function<void()>;

class ShutdownClock {
  public:
    virtual ~ShutdownClock() = default;
    [[nodiscard]] virtual std::chrono::steady_clock::time_point now() noexcept = 0;
};

class ShutdownWait {
  public:
    virtual ~ShutdownWait() = default;
    virtual bool wait_until(
        std::condition_variable& condition,
        std::unique_lock<std::mutex>& lock,
        std::chrono::steady_clock::time_point deadline,
        const std::function<bool()>& ready) noexcept = 0;
};

struct ProducerOptions final {
    std::size_t queue_capacity{default_queue_capacity};
    JournalSinkFactory sink_factory;
    UuidFactory uuid_factory;
    std::shared_ptr<ShutdownClock> shutdown_clock;
    std::shared_ptr<ShutdownWait> shutdown_wait;
    // Called as the final operation on the writer thread. A plugin host may use
    // a non-returning FreeLibraryAndExitThread hook to bind DLL code lifetime.
    WriterThreadExit writer_thread_exit;
};

class JournalProducer final {
  public:
    struct Shared;

    explicit JournalProducer(ProducerOptions options = {});
    ~JournalProducer();

    JournalProducer(const JournalProducer&) = delete;
    JournalProducer& operator=(const JournalProducer&) = delete;
    JournalProducer(JournalProducer&&) = delete;
    JournalProducer& operator=(JournalProducer&&) = delete;

    [[nodiscard]] ProducerResult start_recording(const RecordingStart& start) noexcept;
    [[nodiscard]] CallbackResult submit(JournalSnapshot snapshot) noexcept;
    [[nodiscard]] ProducerResult normal_stop(const RecordingStop& stop) noexcept;
    [[nodiscard]] ProducerResult shutdown() noexcept;

    [[nodiscard]] ProducerResult result() const noexcept;
    [[nodiscard]] ProducerState state() const noexcept;
    [[nodiscard]] std::string recording_session_id() const noexcept;

  private:
    std::shared_ptr<Shared> shared_;
    std::mutex shutdown_mutex_;
    bool shutdown_called_{};
    ProducerResult shutdown_result_{ProducerResult::producer_ok};
    class ThreadHolder;
    std::unique_ptr<ThreadHolder> thread_;
};

class MonotonicQpcClock final {
  public:
    MonotonicQpcClock() noexcept;
    [[nodiscard]] std::uint64_t now_ns() const noexcept;

  private:
    std::int64_t origin_{};
    std::int64_t frequency_{};
};

[[nodiscard]] std::string uuid_v4() noexcept;
[[nodiscard]] bool valid_uuid_v4(std::string_view value) noexcept;
[[nodiscard]] const char* to_string(CallbackResult value) noexcept;
[[nodiscard]] const char* to_string(ProducerResult value) noexcept;
[[nodiscard]] const char* to_string(ProducerState value) noexcept;

}  // namespace matrix_auto_cutter
