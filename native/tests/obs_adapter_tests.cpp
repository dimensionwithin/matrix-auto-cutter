#include "matrix_auto_cutter/obs_adapter.hpp"

#include <Windows.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using namespace matrix_auto_cutter;
using namespace matrix_auto_cutter::obs_adapter;

constexpr std::string_view session_id = "11111111-1111-4111-8111-111111111111";
constexpr std::string_view event_id = "22222222-2222-4222-8222-222222222222";
constexpr std::string_view recording_path = R"(P:\smoke\real-obs.mp4)";

struct TestFailure final : std::runtime_error {
    using std::runtime_error::runtime_error;
};

void check(const bool condition, const std::string_view message) {
    if (!condition) {
        throw TestFailure(std::string(message));
    }
}

template <typename Predicate>
void wait_for(Predicate&& predicate, const std::string_view message) {
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (!predicate()) {
        if (std::chrono::steady_clock::now() >= deadline) {
            throw TestFailure(std::string(message));
        }
        std::this_thread::yield();
    }
}

struct BlockingPoint final {
    std::mutex mutex;
    std::condition_variable changed;
    bool enabled{};
    bool entered{};
    bool released{};

    void enter_if_enabled() {
        std::unique_lock lock(mutex);
        if (!enabled) {
            return;
        }
        entered = true;
        changed.notify_all();
        changed.wait(lock, [&] { return released; });
    }

    void wait_until_entered() {
        std::unique_lock lock(mutex);
        check(changed.wait_for(lock, 2s, [&] { return entered; }), "blocking point not entered");
    }

    void release() {
        {
            std::lock_guard lock(mutex);
            released = true;
        }
        changed.notify_all();
    }
};

struct ProducerState final {
    std::mutex mutex;
    std::condition_variable changed;
    ProducerResult start_result{ProducerResult::producer_ok};
    ProducerResult stop_result{ProducerResult::producer_ok};
    ProducerResult shutdown_result{ProducerResult::producer_ok};
    CallbackResult calibration_result{CallbackResult::accepted};
    unsigned starts{};
    unsigned events{};
    unsigned calibrations{};
    unsigned pauses{};
    unsigned resumes{};
    unsigned stops{};
    unsigned shutdowns{};
    RecordingStart start{};
    RecordingStop stop{};
    BlockingPoint shutdown_block;
};

class FakeProducer final : public ProducerPort {
  public:
    explicit FakeProducer(std::shared_ptr<ProducerState> state) : state_(std::move(state)) {}

    ProducerResult start_recording(const RecordingStart& start) noexcept override {
        try {
            std::lock_guard lock(state_->mutex);
            ++state_->starts;
            state_->start = start;
            state_->changed.notify_all();
            return state_->start_result;
        } catch (...) {
            return ProducerResult::producer_internal_error;
        }
    }

    CallbackResult submit(JournalSnapshot snapshot) noexcept override {
        std::lock_guard lock(state_->mutex);
        if (std::holds_alternative<EventSnapshot>(snapshot)) {
            ++state_->events;
        } else if (std::holds_alternative<CalibrationSnapshot>(snapshot)) {
            ++state_->calibrations;
        } else if (std::holds_alternative<PauseSnapshot>(snapshot)) {
            ++state_->pauses;
        } else {
            ++state_->resumes;
        }
        state_->changed.notify_all();
        return state_->calibration_result;
    }

    ProducerResult normal_stop(const RecordingStop& stop) noexcept override {
        std::lock_guard lock(state_->mutex);
        ++state_->stops;
        state_->stop = stop;
        state_->changed.notify_all();
        return state_->stop_result;
    }

    ProducerResult shutdown() noexcept override {
        state_->shutdown_block.enter_if_enabled();
        std::lock_guard lock(state_->mutex);
        ++state_->shutdowns;
        state_->changed.notify_all();
        return state_->shutdown_result;
    }

    ProducerResult result() const noexcept override {
        std::lock_guard lock(state_->mutex);
        return state_->shutdown_result;
    }

    std::string recording_session_id() const noexcept override {
        std::lock_guard lock(state_->mutex);
        return state_->start.recording_session_id.value_or("");
    }

  private:
    std::shared_ptr<ProducerState> state_;
};

class FakeFactory final : public ProducerFactory {
  public:
    explicit FakeFactory(std::shared_ptr<ProducerState> state) : state_(std::move(state)) {}

    std::unique_ptr<ProducerPort> create() noexcept override {
        creates.fetch_add(1, std::memory_order_relaxed);
        return std::make_unique<FakeProducer>(state_);
    }

    std::atomic<unsigned> creates{};

  private:
    std::shared_ptr<ProducerState> state_;
};

class FakeHost final : public AdapterHost {
  public:
    bool install_callbacks(
        const FrontendCallback frontend,
        const TickCallback tick,
        void* private_data) noexcept override {
        std::lock_guard lock(mutex_);
        frontend_ = frontend;
        tick_ = tick;
        private_data_ = private_data;
        installed = true;
        ++install_calls;
        return install_result;
    }

    void remove_callbacks() noexcept override {
        std::lock_guard lock(mutex_);
        if (installed) {
            installed = false;
            ++remove_calls;
        }
        frontend_ = nullptr;
        tick_ = nullptr;
    }

    bool acquire_recording_output() noexcept override {
        ++acquire_calls;
        if (!acquire_result) {
            return false;
        }
        references.fetch_add(1, std::memory_order_acq_rel);
        acquire_block.enter_if_enabled();
        return true;
    }

    bool connect_recording_output_signals(
        const OutputCallback output,
        void* private_data) noexcept override {
        std::lock_guard lock(mutex_);
        if (!connect_result || output_connected) {
            return false;
        }
        output_ = output;
        output_private_data_ = private_data;
        output_connected = true;
        ++connect_calls;
        return true;
    }

    void disconnect_recording_output_signals() noexcept override {
        std::lock_guard lock(mutex_);
        if (output_connected) {
            output_connected = false;
            output_ = nullptr;
            ++disconnect_calls;
            cleanup_order.emplace_back("disconnect");
        }
    }

    bool capture_recording_output(RecordingSignal& signal) noexcept override {
        capture_output_block.enter_if_enabled();
        if (!capture_result || references.load(std::memory_order_acquire) == 0) {
            return false;
        }
        std::lock_guard lock(mutex_);
        signal = next_signal_;
        return true;
    }

    bool capture_clock(
        std::uint64_t& absolute_monotonic_ns,
        std::uint64_t& output_frame_count) noexcept override {
        capture_clock_block.enter_if_enabled();
        if (!capture_result || references.load(std::memory_order_acquire) == 0) {
            return false;
        }
        std::lock_guard lock(mutex_);
        absolute_monotonic_ns = next_signal_.absolute_monotonic_ns;
        output_frame_count = next_signal_.output_frame_count;
        return true;
    }

    void release_recording_output() noexcept override {
        unsigned current = references.load(std::memory_order_acquire);
        while (current > 0 && !references.compare_exchange_weak(
                                  current,
                                  current - 1,
                                  std::memory_order_acq_rel,
                                  std::memory_order_acquire)) {
        }
        if (current > 0) {
            ++release_calls;
            std::lock_guard lock(mutex_);
            cleanup_order.emplace_back("release");
        }
    }

    std::string_view obs_version() const noexcept override { return "32.1.2"; }

    void log(LogLevel, const std::string_view message) noexcept override {
        try {
            std::lock_guard lock(mutex_);
            logs.emplace_back(message);
        } catch (...) {
        }
    }

    void set_signal(
        const std::string_view path,
        const std::uint64_t absolute_ns,
        const std::uint64_t frames,
        const std::string_view output_id = "ffmpeg_muxer",
        const bool fragmented = false) {
        std::lock_guard lock(mutex_);
        check(next_signal_.path.assign(path), "test path was not bounded");
        check(next_signal_.output_id.assign(output_id), "test output id was not bounded");
        next_signal_.absolute_monotonic_ns = absolute_ns;
        next_signal_.output_frame_count = frames;
        next_signal_.fragmented_mp4 = fragmented;
    }

    void fire_frontend(const FrontendEvent event) noexcept {
        FrontendCallback callback{};
        void* data{};
        {
            std::lock_guard lock(mutex_);
            callback = frontend_;
            data = private_data_;
        }
        if (callback != nullptr) {
            callback(event, data);
        }
    }

    void fire_tick() noexcept {
        TickCallback callback{};
        void* data{};
        {
            std::lock_guard lock(mutex_);
            callback = tick_;
            data = private_data_;
        }
        if (callback != nullptr) {
            callback(data);
        }
    }

    void fire_output(const OutputEvent event, const int code = 0) noexcept {
        OutputCallback callback{};
        void* data{};
        {
            std::lock_guard lock(mutex_);
            callback = output_;
            data = output_private_data_;
        }
        if (callback != nullptr) {
            callback(event, code, data);
        }
    }

    bool install_result{true};
    bool acquire_result{true};
    bool connect_result{true};
    bool capture_result{true};
    bool installed{};
    bool output_connected{};
    std::atomic<unsigned> install_calls{};
    std::atomic<unsigned> remove_calls{};
    std::atomic<unsigned> acquire_calls{};
    std::atomic<unsigned> connect_calls{};
    std::atomic<unsigned> disconnect_calls{};
    std::atomic<unsigned> release_calls{};
    std::atomic<unsigned> references{};
    BlockingPoint acquire_block;
    BlockingPoint capture_output_block;
    BlockingPoint capture_clock_block;
    std::vector<std::string> cleanup_order;
    std::vector<std::string> logs;

  private:
    mutable std::mutex mutex_;
    RecordingSignal next_signal_{};
    FrontendCallback frontend_{};
    TickCallback tick_{};
    OutputCallback output_{};
    void* private_data_{};
    void* output_private_data_{};
};

struct ModuleCounters final {
    std::atomic<int> pins{};
    std::atomic<unsigned> callback_releases{};
    std::atomic<unsigned> worker_releases{};
};

class FakeCallbackLifetime final : public CallbackRegistrationLifetime {
  public:
    explicit FakeCallbackLifetime(std::shared_ptr<ModuleCounters> state) : state_(std::move(state)) {
        state_->pins.fetch_add(1, std::memory_order_acq_rel);
    }
    ~FakeCallbackLifetime() override {
        state_->callback_releases.fetch_add(1, std::memory_order_acq_rel);
        state_->pins.fetch_sub(1, std::memory_order_acq_rel);
    }

  private:
    std::shared_ptr<ModuleCounters> state_;
};

class FakeWorkerLifetime final : public WorkerThreadLifetime {
  public:
    explicit FakeWorkerLifetime(std::shared_ptr<ModuleCounters> state) : state_(std::move(state)) {
        state_->pins.fetch_add(1, std::memory_order_acq_rel);
    }
    void exit_thread() noexcept override {
        state_->worker_releases.fetch_add(1, std::memory_order_acq_rel);
        state_->pins.fetch_sub(1, std::memory_order_acq_rel);
    }

  private:
    std::shared_ptr<ModuleCounters> state_;
};

struct TempRoot final {
    TempRoot() {
        static std::atomic<unsigned> serial{};
        path = std::filesystem::temp_directory_path() /
               std::filesystem::path(
                   L"matrix-obs-adapter-tests-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
                   std::to_wstring(serial.fetch_add(1, std::memory_order_relaxed)));
    }
    ~TempRoot() {
        std::error_code error;
        std::filesystem::remove_all(path, error);
    }
    std::filesystem::path path;
};

AdapterOptions test_options(
    const std::filesystem::path& local_app_data,
    const std::shared_ptr<ModuleCounters>& module = {}) {
    AdapterOptions options;
    auto session_calls = std::make_shared<std::atomic<unsigned>>(0);
    options.session_uuid_factory = [session_calls] {
        session_calls->fetch_add(1, std::memory_order_relaxed);
        return std::string(session_id);
    };
    options.event_uuid_factory = [] { return std::string(event_id); };
    options.local_app_data_provider = [local_app_data] {
        return std::optional<std::filesystem::path>(local_app_data);
    };
    if (module) {
        options.callback_lifetime_factory = [module] {
            return std::make_unique<FakeCallbackLifetime>(module);
        };
        options.worker_lifetime_factory = [module] {
            return std::make_unique<FakeWorkerLifetime>(module);
        };
    }
    return options;
}

void wait_for_producer_count(
    const std::shared_ptr<ProducerState>& state,
    const std::function<bool(const ProducerState&)>& ready,
    const std::string_view message) {
    std::unique_lock lock(state->mutex);
    check(state->changed.wait_for(lock, 2s, [&] { return ready(*state); }), message);
}

void bind_output(FakeHost& host, ObsJournalAdapter& adapter) {
    host.fire_frontend(FrontendEvent::recording_starting);
    check(adapter.state() == AdapterState::start_pending, "STARTING did not enter start_pending");
    check(host.references.load(std::memory_order_acquire) == 1, "output reference not acquired");
    check(host.connect_calls.load(std::memory_order_acquire) == 1, "signals not connected once");
}

void start_output(
    FakeHost& host,
    ObsJournalAdapter& adapter,
    const std::shared_ptr<ProducerState>& producer) {
    bind_output(host, adapter);
    host.set_signal(recording_path, 10'000'000'000ULL, 7);
    host.fire_output(OutputEvent::started);
    wait_for_producer_count(producer, [](const auto& value) { return value.starts == 1; },
                            "actual output start did not start producer");
    wait_for([&] { return adapter.state() == AdapterState::active; }, "adapter did not activate");
}

void anchor_recording(
    FakeHost& host,
    const std::shared_ptr<ProducerState>& producer) {
    host.set_signal(recording_path, 10'016'666'666ULL, 8);
    host.fire_tick();
    wait_for_producer_count(producer, [](const auto& value) { return value.events == 1; },
                            "recording_started was not anchored");
}

void stop_output_successfully(
    FakeHost& host,
    ObsJournalAdapter& adapter,
    const std::shared_ptr<ProducerState>& producer) {
    host.set_signal(recording_path, 14'000'000'000ULL, 248);
    host.fire_output(OutputEvent::stopped, 0);
    wait_for_producer_count(producer, [](const auto& value) { return value.shutdowns == 1; },
                            "successful output stop did not shut down producer");
    wait_for([&] { return adapter.state() == AdapterState::idle; }, "normal stop did not return idle");
}

void test_output_lifecycle_authority_normative_path_and_exact_binding() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "load failed");

    host.fire_frontend(FrontendEvent::recording_started);
    check(factory.creates.load(std::memory_order_acquire) == 0,
          "frontend RECORDING_STARTED alone created a producer");
    bind_output(host, adapter);
    check(factory.creates.load(std::memory_order_acquire) == 0,
          "frontend STARTING created a producer before output start");
    check(!std::filesystem::exists(
              root.path / L"DimensionWithin" / L"MatrixAutoCutter" / L"producer" / L"journals"),
          "STARTING performed journal directory IO in the callback");

    host.fire_frontend(FrontendEvent::recording_started);
    check(factory.creates.load(std::memory_order_acquire) == 0,
          "frontend STARTED became journal authority");
    host.set_signal(recording_path, 10'000'000'000ULL, 7);
    host.fire_output(OutputEvent::started);
    wait_for_producer_count(producer, [](const auto& value) { return value.starts == 1; },
                            "output start did not create producer");
    wait_for([&] { return adapter.state() == AdapterState::active; }, "adapter did not activate");
    {
        std::lock_guard lock(producer->mutex);
        const auto expected_directory = root.path / L"DimensionWithin" / L"MatrixAutoCutter" /
                                        L"producer" / L"journals";
        check(producer->start.journal_path.parent_path() == expected_directory,
              "journal was not placed under normative LocalAppData producer directory");
        check(producer->start.recording_session_id == session_id,
              "adapter did not pass its explicit session to the producer");
        check(producer->start.journal_path.filename() ==
                  std::filesystem::path(
                      std::string(session_id) + ".recording-journal.ndjson"),
              "journal filename was not exactly session-bound");
    }

    anchor_recording(host, producer);
    host.set_signal(recording_path, 12'016'666'666ULL, 128);
    host.fire_tick();
    wait_for_producer_count(producer, [](const auto& value) { return value.calibrations == 1; },
                            "calibration was not accepted");

    host.fire_frontend(FrontendEvent::recording_stopped);
    {
        std::lock_guard lock(producer->mutex);
        check(producer->stops == 0, "frontend STOPPED authorized a normal stop");
    }
    check(adapter.state() == AdapterState::active,
          "frontend STOPPED changed the authoritative output lifecycle");

    stop_output_successfully(host, adapter, producer);
    const auto report = adapter.last_report();
    check(report.has_value() && report->successful, "successful output stop was not reportable");
    check(report->recording_session_id == session_id, "report session binding changed");
    check(report->final_frame_count == 248, "final output counter changed");
    check(host.disconnect_calls.load(std::memory_order_acquire) == 1,
          "output signal connections were not removed exactly once");
    check(host.release_calls.load(std::memory_order_acquire) == 1,
          "output reference was not released exactly once");
    check(host.cleanup_order == std::vector<std::string>({"disconnect", "release"}),
          "output reference was released before callback disconnection");

    host.fire_output(OutputEvent::stopped, 0);
    adapter.unload();
    adapter.unload();
    check(host.remove_calls.load(std::memory_order_acquire) == 1,
          "repeated unload removed frontend/tick callbacks twice");
    check(host.disconnect_calls.load(std::memory_order_acquire) == 1,
          "repeated stop/unload disconnected output twice");
    check(host.release_calls.load(std::memory_order_acquire) == 1,
          "repeated stop/unload released output twice");
}

void test_failed_or_missing_output_stop_never_calls_normal_stop() {
    {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options(root.path));
        check(adapter.load(), "failed-stop load failed");
        start_output(host, adapter, producer);
        anchor_recording(host, producer);
        host.set_signal(recording_path, 14'000'000'000ULL, 248);
        host.fire_output(OutputEvent::stopped, -4);
        wait_for([&] { return adapter.state() == AdapterState::failed; },
                 "failed output stop did not fail run");
        {
            std::lock_guard lock(producer->mutex);
            check(producer->stops == 0, "failed output stop emitted normal stop");
            check(producer->shutdowns == 1, "failed output stop did not clean producer");
        }
        adapter.unload();
    }
    {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options(root.path));
        check(adapter.load(), "missing-stop load failed");
        start_output(host, adapter, producer);
        anchor_recording(host, producer);
        host.fire_frontend(FrontendEvent::recording_stopped);
        adapter.unload();
        std::lock_guard lock(producer->mutex);
        check(producer->stops == 0, "missing output stop emitted normal stop during unload");
        check(producer->shutdowns == 1, "missing output stop did not shut producer down");
    }
}

void test_actual_output_pause_resume_is_ordered_and_gates_calibration() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "pause/resume load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_signal(recording_path, 12'000'000'000ULL, 120);
    host.fire_output(OutputEvent::paused);
    wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 1; },
                            "actual output pause did not submit one pause snapshot");
    check(adapter.state() == AdapterState::paused, "pause did not move adapter to paused state");
    host.set_signal(recording_path, 14'000'000'000ULL, 120);
    host.fire_tick();
    std::this_thread::sleep_for(10ms);
    {
        std::lock_guard lock(producer->mutex);
        check(producer->calibrations == 0, "calibration was accepted during pause");
    }

    host.set_signal(recording_path, 15'000'000'000ULL, 122);
    host.fire_output(OutputEvent::resumed);
    wait_for_producer_count(producer, [](const auto& value) { return value.resumes == 1; },
                            "actual output unpause did not submit one resume snapshot");
    wait_for([&] { return adapter.state() == AdapterState::active; },
             "resume did not reactivate adapter");
    host.set_signal(recording_path, 17'100'000'000ULL, 248);
    host.fire_tick();
    wait_for_producer_count(producer, [](const auto& value) { return value.calibrations == 1; },
                            "calibration did not continue after resume");
    stop_output_successfully(host, adapter, producer);
    std::lock_guard lock(producer->mutex);
    check(!producer->stop.clock.recording_paused, "stop after resume retained paused flag");
}

void test_pause_sequence_failure_and_stop_while_paused() {
    {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options(root.path));
        check(adapter.load(), "double-pause load failed");
        start_output(host, adapter, producer);
        anchor_recording(host, producer);
        host.set_signal(recording_path, 12'000'000'000ULL, 120);
        host.fire_output(OutputEvent::paused);
        host.fire_output(OutputEvent::paused);
        wait_for([&] { return adapter.state() == AdapterState::failed; },
                 "double actual pause was not fail closed");
        adapter.unload();
    }
    {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options(root.path));
        check(adapter.load(), "paused-stop load failed");
        start_output(host, adapter, producer);
        anchor_recording(host, producer);
        host.set_signal(recording_path, 12'000'000'000ULL, 120);
        host.fire_output(OutputEvent::paused);
        wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 1; },
                                "pause was not accepted before stop");
        host.set_signal(recording_path, 15'000'000'000ULL, 121);
        host.fire_output(OutputEvent::stopped, 0);
        wait_for([&] { return adapter.state() == AdapterState::idle; },
                 "stop while paused did not finish");
        std::lock_guard lock(producer->mutex);
        check(producer->stop.clock.recording_paused,
              "stop while paused did not retain paused flag");
    }
}

void test_invalid_session_localappdata_and_unsupported_or_concurrent_start_fail_closed() {
    {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        auto options = test_options(root.path);
        options.session_uuid_factory = [] {
            return std::string("11111111-1111-5111-8111-111111111111");
        };
        ObsJournalAdapter adapter(host, factory, std::move(options));
        check(adapter.load(), "invalid-session load failed");
        bind_output(host, adapter);
        host.set_signal(recording_path, 10'000'000'000ULL, 7);
        host.fire_output(OutputEvent::started);
        wait_for([&] { return adapter.state() == AdapterState::failed; },
                 "invalid explicit session did not fail closed");
        check(factory.creates.load(std::memory_order_acquire) == 0,
              "invalid explicit session reached producer allocation");
        check(host.references.load(std::memory_order_acquire) == 0,
              "invalid explicit session leaked output reference");
        adapter.unload();
    }
    {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        auto options = test_options(root.path);
        options.local_app_data_provider = [] {
            return std::optional<std::filesystem::path>{};
        };
        ObsJournalAdapter adapter(host, factory, std::move(options));
        check(adapter.load(), "missing-LocalAppData load failed");
        bind_output(host, adapter);
        host.set_signal(recording_path, 10'000'000'000ULL, 7);
        host.fire_output(OutputEvent::started);
        wait_for([&] { return adapter.state() == AdapterState::failed; },
                 "missing LocalAppData did not fail closed");
        check(factory.creates.load(std::memory_order_acquire) == 0,
              "missing LocalAppData reached producer allocation");
        adapter.unload();
    }
    const auto unsupported_output_fails_at_actual_start = [](
                                                             const std::string_view path,
                                                             const std::string_view output_id,
                                                             const bool fragmented) {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options(root.path));
        check(adapter.load(), "unsupported-start load failed");
        host.set_signal(path, 1, 0, output_id, fragmented);
        host.fire_frontend(FrontendEvent::recording_starting);
        check(adapter.state() == AdapterState::start_pending,
              "STARTING did not bind the current output before its settings were authoritative");
        check(host.references.load(std::memory_order_acquire) == 1,
              "STARTING did not retain the current output");
        host.fire_output(OutputEvent::started);
        wait_for([&] { return adapter.state() == AdapterState::failed; },
                 "unsupported actual output start did not fail closed");
        check(factory.creates.load(std::memory_order_acquire) == 0,
              "unsupported output reached producer allocation");
        check(host.references.load(std::memory_order_acquire) == 0,
              "unsupported output leaked its retained reference");
        adapter.unload();
    };
    unsupported_output_fails_at_actual_start(R"(P:\smoke\unsupported.mkv)", "ffmpeg_muxer", false);
    unsupported_output_fails_at_actual_start(recording_path, "mp4_output", false);
    unsupported_output_fails_at_actual_start(recording_path, "ffmpeg_muxer", true);
    {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options(root.path));
        check(adapter.load(), "concurrent-start load failed");
        bind_output(host, adapter);
        host.fire_frontend(FrontendEvent::recording_starting);
        check(host.acquire_calls.load(std::memory_order_acquire) == 1,
              "concurrent start acquired a second output");
        check(host.references.load(std::memory_order_acquire) == 1,
              "concurrent start changed reference ownership");
        adapter.unload();
    }
}

enum class BlockedScenario { idle_frontend, start_pending, active_tick, output_stop };

void run_blocked_unload_scenario(const BlockedScenario scenario) {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    auto module = std::make_shared<ModuleCounters>();
    FakeFactory factory(producer);
    FakeHost host;
    auto options = test_options(root.path, module);
    auto callback_block = std::make_shared<BlockingPoint>();
    if (scenario == BlockedScenario::idle_frontend) {
        callback_block->enabled = true;
        options.callback_probe = [callback_block](const CallbackKind kind) {
            if (kind == CallbackKind::frontend) {
                callback_block->enter_if_enabled();
            }
        };
    } else if (scenario == BlockedScenario::active_tick) {
        options.callback_probe = [callback_block](const CallbackKind kind) {
            if (kind == CallbackKind::tick) {
                callback_block->enter_if_enabled();
            }
        };
    }
    options.unload_deadline = 1ms;
    if (scenario == BlockedScenario::start_pending) {
        host.acquire_block.enabled = true;
    }
    ObsJournalAdapter adapter(host, factory, std::move(options));
    check(adapter.load(), "blocked-unload load failed");

    if (scenario == BlockedScenario::active_tick || scenario == BlockedScenario::output_stop) {
        start_output(host, adapter, producer);
        anchor_recording(host, producer);
    }

    if (scenario == BlockedScenario::active_tick) {
        callback_block->enabled = true;
    }
    if (scenario == BlockedScenario::output_stop) {
        host.capture_output_block.enabled = true;
    }

    std::thread callback;
    BlockingPoint* active_block = callback_block.get();
    if (scenario == BlockedScenario::idle_frontend) {
        callback = std::thread([&] { host.fire_frontend(FrontendEvent::other); });
    } else if (scenario == BlockedScenario::start_pending) {
        active_block = &host.acquire_block;
        host.set_signal(recording_path, 9'900'000'000ULL, 0);
        callback = std::thread([&] { host.fire_frontend(FrontendEvent::recording_starting); });
    } else if (scenario == BlockedScenario::active_tick) {
        callback = std::thread([&] { host.fire_tick(); });
    } else {
        active_block = &host.capture_output_block;
        host.set_signal(recording_path, 14'000'000'000ULL, 248);
        callback = std::thread([&] { host.fire_output(OutputEvent::stopped, 0); });
    }
    active_block->wait_until_entered();
    if (scenario == BlockedScenario::start_pending) {
        check(adapter.state() == AdapterState::start_pending,
              "blocked acquire was not visibly start_pending");
    }
    adapter.unload();
    check(module->pins.load(std::memory_order_acquire) == 2,
          "callback/worker DLL pins were released at unload timeout");
    check(module->callback_releases.load(std::memory_order_acquire) == 0,
          "callback registration pin released before blocked callback exit");
    check(module->worker_releases.load(std::memory_order_acquire) == 0,
          "worker pin released before blocked callback exit");
    active_block->release();
    callback.join();
    wait_for([&] { return module->pins.load(std::memory_order_acquire) == 0; },
             "module pins did not drain after last callback/thread exit");
    check(module->callback_releases.load(std::memory_order_acquire) == 1,
          "callback registration module ref was not released exactly once");
    check(module->worker_releases.load(std::memory_order_acquire) == 1,
          "worker module ref was not released exactly once");
}

void test_unload_drains_blocked_callbacks_in_all_adapter_states() {
    run_blocked_unload_scenario(BlockedScenario::idle_frontend);
    run_blocked_unload_scenario(BlockedScenario::start_pending);
    run_blocked_unload_scenario(BlockedScenario::active_tick);
    run_blocked_unload_scenario(BlockedScenario::output_stop);
}

void test_adapter_worker_timeout_keeps_module_pinned_until_shutdown_returns() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->shutdown_block.enabled = true;
    auto module = std::make_shared<ModuleCounters>();
    FakeFactory factory(producer);
    FakeHost host;
    auto options = test_options(root.path, module);
    options.unload_deadline = 1ms;
    ObsJournalAdapter adapter(host, factory, std::move(options));
    check(adapter.load(), "worker-timeout load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);
    adapter.unload();
    producer->shutdown_block.wait_until_entered();
    check(module->pins.load(std::memory_order_acquire) == 2,
          "worker/callback registration pins did not survive worker timeout");
    producer->shutdown_block.release();
    wait_for([&] { return module->pins.load(std::memory_order_acquire) == 0; },
             "module pins did not release after worker shutdown completed");
    check(module->callback_releases.load(std::memory_order_acquire) == 1 &&
              module->worker_releases.load(std::memory_order_acquire) == 1,
          "worker-timeout lifetime released a pin more than once");
}

void test_no_exception_crosses_any_callback_boundary() {
    ObsJournalAdapter::frontend_boundary(FrontendEvent::recording_starting, nullptr);
    ObsJournalAdapter::tick_boundary(nullptr);
    ObsJournalAdapter::output_boundary(OutputEvent::started, 0, nullptr);

    for (const auto kind : {CallbackKind::frontend, CallbackKind::tick,
                            CallbackKind::output_start, CallbackKind::output_stop}) {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        auto options = test_options(root.path);
        options.callback_probe = [kind](const CallbackKind observed) {
            if (observed == kind) {
                throw std::runtime_error("injected callback exception");
            }
        };
        ObsJournalAdapter adapter(host, factory, std::move(options));
        check(adapter.load(), "exception-boundary load failed");
        if (kind == CallbackKind::frontend) {
            host.fire_frontend(FrontendEvent::other);
        } else if (kind == CallbackKind::output_start) {
            bind_output(host, adapter);
            host.set_signal(recording_path, 10'000'000'000ULL, 7);
            host.fire_output(OutputEvent::started, 0);
        } else {
            start_output(host, adapter, producer);
            if (kind == CallbackKind::tick) {
                host.fire_tick();
            } else {
                host.fire_output(OutputEvent::stopped, 0);
            }
        }
        adapter.unload();
    }
}

}  // namespace

int main() {
    std::cout << std::unitbuf;
    std::cerr << std::unitbuf;
    const std::vector<std::pair<const char*, std::function<void()>>> tests{
        {"output-authority/path/session/calibration/stop/cleanup/idempotence",
         test_output_lifecycle_authority_normative_path_and_exact_binding},
        {"failed-or-missing-output-stop", test_failed_or_missing_output_stop_never_calls_normal_stop},
        {"actual-output-pause-resume/calibration-gate/order",
         test_actual_output_pause_resume_is_ordered_and_gates_calibration},
        {"pause-sequence-failure/stop-while-paused", test_pause_sequence_failure_and_stop_while_paused},
        {"invalid-session/localappdata/unsupported/concurrent-start",
         test_invalid_session_localappdata_and_unsupported_or_concurrent_start_fail_closed},
        {"unload-blocked-idle/start-pending/active-tick/output-stop",
         test_unload_drains_blocked_callbacks_in_all_adapter_states},
        {"adapter-worker-timeout-module-pin",
         test_adapter_worker_timeout_keeps_module_pinned_until_shutdown_returns},
        {"no-exception-C-boundaries", test_no_exception_crosses_any_callback_boundary},
    };
    int failures = 0;
    for (const auto& [name, test] : tests) {
        std::cout << "RUN " << name << '\n';
        try {
            test();
            std::cout << "PASS " << name << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL " << name << ": " << error.what() << '\n';
        } catch (...) {
            ++failures;
            std::cerr << "FAIL " << name << ": unknown exception\n";
        }
    }
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
