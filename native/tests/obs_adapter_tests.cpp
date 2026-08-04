#include "matrix_auto_cutter/obs_adapter.hpp"

#include <Windows.h>

#include <algorithm>
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

std::string indexed_event_uuid(const unsigned index) {
    std::string value = "22222222-2222-4222-8222-222222222220";
    constexpr char hex[] = "0123456789abcdef";
    value.back() = hex[index % 16U];
    return value;
}

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
    bool one_shot{};
    bool entered{};
    bool released{};

    void enter_if_enabled() {
        std::unique_lock lock(mutex);
        if (!enabled) {
            return;
        }
        if (one_shot) {
            enabled = false;
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

struct AcceptedSnapshot final {
    std::string kind;
    ClockSnapshot clock;
};

struct ProducerState final {
    std::mutex mutex;
    std::condition_variable changed;
    ProducerResult start_result{ProducerResult::producer_ok};
    ProducerResult stop_result{ProducerResult::producer_ok};
    ProducerResult shutdown_result{ProducerResult::producer_ok};
    ProducerResult durable_result{ProducerResult::producer_ok};
    unsigned fail_durable_confirm_call{};
    CallbackResult calibration_result{CallbackResult::accepted};
    unsigned starts{};
    unsigned events{};
    unsigned calibrations{};
    unsigned pauses{};
    unsigned resumes{};
    unsigned stops{};
    unsigned shutdowns{};
    unsigned durable_confirms{};
    RecordingStart start{};
    RecordingStop stop{};
    std::vector<EventSnapshot> event_snapshots;
    std::vector<AcceptedSnapshot> accepted_snapshots;
    std::optional<ClockSnapshot> simulated_previous_clock;
    WriterFailure simulated_writer_failure{WriterFailure::none};
    bool simulated_paused{};
    bool enforce_writer_order{};
    BlockingPoint shutdown_block;
    BlockingPoint pause_submit_block;
    BlockingPoint scene_submit_block;
    BlockingPoint durable_confirm_block;
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
        if (std::holds_alternative<PauseSnapshot>(snapshot)) {
            state_->pause_submit_block.enter_if_enabled();
        }
        if (const auto* event = std::get_if<EventSnapshot>(&snapshot);
            event != nullptr && event->event_type == EventType::scene_changed) {
            state_->scene_submit_block.enter_if_enabled();
        }
        std::lock_guard lock(state_->mutex);
        const auto clock = std::visit([](const auto& value) { return value.clock; }, snapshot);
        const bool is_pause = std::holds_alternative<PauseSnapshot>(snapshot);
        const bool is_resume = std::holds_alternative<ResumeSnapshot>(snapshot);
        const bool requires_active = std::holds_alternative<EventSnapshot>(snapshot) ||
                                     std::holds_alternative<CalibrationSnapshot>(snapshot);
        std::string kind;
        if (const auto* event = std::get_if<EventSnapshot>(&snapshot)) {
            ++state_->events;
            state_->event_snapshots.push_back(*event);
            kind = event->event_type == EventType::recording_started ? "recording_started"
                                                                     : "scene_changed";
        } else if (std::holds_alternative<CalibrationSnapshot>(snapshot)) {
            ++state_->calibrations;
            kind = "calibration";
        } else if (std::holds_alternative<PauseSnapshot>(snapshot)) {
            ++state_->pauses;
            kind = "pause";
        } else {
            ++state_->resumes;
            kind = "resume";
        }
        if (state_->enforce_writer_order &&
            state_->calibration_result == CallbackResult::accepted) {
            if (state_->simulated_previous_clock.has_value() &&
                clock.monotonic_ns < state_->simulated_previous_clock->monotonic_ns) {
                state_->simulated_writer_failure = WriterFailure::qpc_regression;
            } else if (state_->simulated_previous_clock.has_value() &&
                       clock.output_frame_count <
                           state_->simulated_previous_clock->output_frame_count) {
                state_->simulated_writer_failure = WriterFailure::counter_regression;
            } else if (requires_active && state_->simulated_paused) {
                state_->simulated_writer_failure = WriterFailure::active_snapshot_while_paused;
            } else if (is_pause && state_->simulated_paused) {
                state_->simulated_writer_failure = WriterFailure::pause_while_paused;
            } else if (is_resume && !state_->simulated_paused) {
                state_->simulated_writer_failure = WriterFailure::resume_while_active;
            }
            if (state_->simulated_writer_failure != WriterFailure::none) {
                state_->changed.notify_all();
                return CallbackResult::terminal;
            }
            state_->accepted_snapshots.push_back(AcceptedSnapshot{kind, clock});
            state_->simulated_previous_clock = clock;
            if (is_pause) {
                state_->simulated_paused = true;
            } else if (is_resume) {
                state_->simulated_paused = false;
            }
        }
        state_->changed.notify_all();
        return state_->calibration_result;
    }

    ProducerResult normal_stop(const RecordingStop& stop) noexcept override {
        std::lock_guard lock(state_->mutex);
        ++state_->stops;
        state_->stop = stop;
        if (state_->enforce_writer_order) {
            if (state_->simulated_previous_clock.has_value() &&
                stop.clock.monotonic_ns < state_->simulated_previous_clock->monotonic_ns) {
                state_->simulated_writer_failure = WriterFailure::qpc_regression;
            } else if (state_->simulated_previous_clock.has_value() &&
                       stop.clock.output_frame_count <
                           state_->simulated_previous_clock->output_frame_count) {
                state_->simulated_writer_failure = WriterFailure::counter_regression;
            }
            if (state_->simulated_writer_failure != WriterFailure::none) {
                state_->changed.notify_all();
                return ProducerResult::producer_internal_error;
            }
            state_->accepted_snapshots.push_back(AcceptedSnapshot{"stop", stop.clock});
            state_->simulated_previous_clock = stop.clock;
        }
        state_->changed.notify_all();
        return state_->stop_result;
    }

    ProducerResult confirm_durable() noexcept override {
        state_->durable_confirm_block.enter_if_enabled();
        std::lock_guard lock(state_->mutex);
        ++state_->durable_confirms;
        state_->changed.notify_all();
        return state_->fail_durable_confirm_call == 0 ||
                       state_->durable_confirms == state_->fail_durable_confirm_call
                   ? state_->durable_result
                   : ProducerResult::producer_ok;
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

    matrix_auto_cutter::ProducerStatus status() const noexcept override {
        std::lock_guard lock(state_->mutex);
        matrix_auto_cutter::ProducerStatus status;
        status.state = matrix_auto_cutter::ProducerState::recording_active;
        status.result = state_->shutdown_result;
        status.writer_failure = state_->simulated_writer_failure;
        return status;
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
        stale_frontend_ = frontend;
        tick_ = tick;
        private_data_ = private_data;
        stale_private_data_ = private_data;
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

    SceneHandle acquire_current_program_scene() noexcept override {
        ++scene_acquire_calls;
        if (!scene_available.load(std::memory_order_acquire)) {
            return nullptr;
        }
        scene_references.fetch_add(1, std::memory_order_acq_rel);
        scene_acquire_block.enter_if_enabled();
        return &scene_token_;
    }

    std::string_view scene_uuid(const SceneHandle scene) noexcept override {
        ++scene_uuid_calls;
        if (scene != &scene_token_ || scene_references.load(std::memory_order_acquire) == 0) {
            return {};
        }
        std::lock_guard lock(mutex_);
        return scene_uuid_;
    }

    std::string_view scene_name(const SceneHandle scene) noexcept override {
        ++scene_name_calls;
        if (scene != &scene_token_ || scene_references.load(std::memory_order_acquire) == 0) {
            return {};
        }
        std::lock_guard lock(mutex_);
        return scene_name_;
    }

    void release_scene(const SceneHandle scene) noexcept override {
        ++scene_release_calls;
        if (scene != &scene_token_) {
            ++scene_release_errors;
            return;
        }
        unsigned current = scene_references.load(std::memory_order_acquire);
        while (current > 0 && !scene_references.compare_exchange_weak(
                                  current,
                                  current - 1,
                                  std::memory_order_acq_rel,
                                  std::memory_order_acquire)) {
        }
        if (current == 0) {
            ++scene_release_errors;
        }
    }

    bool capture_clock(
        std::uint64_t& absolute_monotonic_ns,
        std::uint64_t& output_frame_count) noexcept override {
        capture_clock_block.enter_if_enabled();
        if (!capture_result || !capture_clock_result ||
            references.load(std::memory_order_acquire) == 0) {
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

    void set_scene(const std::string_view uuid, const std::string_view name) {
        std::lock_guard lock(mutex_);
        scene_uuid_.assign(uuid);
        scene_name_.assign(name);
    }

    bool has_log(const std::string_view text) const {
        std::lock_guard lock(mutex_);
        return std::any_of(logs.begin(), logs.end(), [&](const auto& line) {
            return line.find(text) != std::string::npos;
        });
    }

    std::string joined_logs() const {
        std::lock_guard lock(mutex_);
        std::string result;
        for (const auto& line : logs) {
            result += line;
            result.push_back('\n');
        }
        return result;
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

    void fire_stale_frontend(const FrontendEvent event) noexcept {
        if (stale_frontend_ != nullptr) {
            stale_frontend_(event, stale_private_data_);
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
    std::atomic<bool> capture_result{true};
    std::atomic<bool> capture_clock_result{true};
    std::atomic<bool> scene_available{true};
    bool installed{};
    bool output_connected{};
    std::atomic<unsigned> install_calls{};
    std::atomic<unsigned> remove_calls{};
    std::atomic<unsigned> acquire_calls{};
    std::atomic<unsigned> connect_calls{};
    std::atomic<unsigned> disconnect_calls{};
    std::atomic<unsigned> release_calls{};
    std::atomic<unsigned> references{};
    std::atomic<unsigned> scene_acquire_calls{};
    std::atomic<unsigned> scene_uuid_calls{};
    std::atomic<unsigned> scene_name_calls{};
    std::atomic<unsigned> scene_release_calls{};
    std::atomic<unsigned> scene_release_errors{};
    std::atomic<unsigned> scene_references{};
    BlockingPoint acquire_block;
    BlockingPoint capture_output_block;
    BlockingPoint capture_clock_block;
    BlockingPoint scene_acquire_block;
    std::vector<std::string> cleanup_order;
    std::vector<std::string> logs;

  private:
    mutable std::mutex mutex_;
    RecordingSignal next_signal_{};
    int scene_token_{};
    std::string scene_uuid_{"444eb885-e589-4338-832c-8f5fd7eaaf41"};
    std::string scene_name_{"Outro"};
    FrontendCallback frontend_{};
    FrontendCallback stale_frontend_{};
    TickCallback tick_{};
    OutputCallback output_{};
    void* private_data_{};
    void* stale_private_data_{};
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

AdapterOptions ordered_test_options(
    const std::filesystem::path& local_app_data,
    const std::shared_ptr<ModuleCounters>& module = {}) {
    auto options = test_options(local_app_data, module);
    auto uuid_index = std::make_shared<std::atomic<unsigned>>(0);
    options.event_uuid_factory = [uuid_index] {
        return indexed_event_uuid(uuid_index->fetch_add(1, std::memory_order_relaxed));
    };
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
    const std::shared_ptr<ProducerState>& producer,
    const std::uint64_t absolute_monotonic_ns = 14'000'000'000ULL,
    const std::uint64_t output_frame_count = 248) {
    host.set_signal(recording_path, absolute_monotonic_ns, output_frame_count);
    host.fire_output(OutputEvent::stopped, 0);
    wait_for_producer_count(producer, [](const auto& value) { return value.shutdowns == 1; },
                            "successful output stop did not shut down producer");
    wait_for([&] { return adapter.state() == AdapterState::idle; }, "normal stop did not return idle");
}

std::vector<EventSnapshot> scene_snapshots(const std::shared_ptr<ProducerState>& producer) {
    std::lock_guard lock(producer->mutex);
    std::vector<EventSnapshot> result;
    for (const auto& event : producer->event_snapshots) {
        if (event.event_type == EventType::scene_changed) {
            result.push_back(event);
        }
    }
    return result;
}

std::string rapid_scene_uuid(unsigned index);

std::vector<std::string> accepted_kinds(const std::shared_ptr<ProducerState>& producer) {
    std::lock_guard lock(producer->mutex);
    std::vector<std::string> result;
    for (const auto& snapshot : producer->accepted_snapshots) {
        if (snapshot.kind != "recording_started") {
            result.push_back(snapshot.kind);
        }
    }
    return result;
}

void check_no_simulated_writer_failure(
    const std::shared_ptr<ProducerState>& producer,
    const std::string_view message) {
    std::lock_guard lock(producer->mutex);
    check(producer->simulated_writer_failure == WriterFailure::none, message);
}

void run_scene_before_pause_order_test(const bool same_clock) {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->enforce_writer_order = true;
    producer->scene_submit_block.enabled = true;
    producer->scene_submit_block.one_shot = true;
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, ordered_test_options(root.path));
    check(adapter.load(), "scene-before-pause load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_scene(rapid_scene_uuid(0), "Worker blocker");
    host.set_signal(recording_path, 10'200'000'000ULL, 20);
    host.fire_frontend(FrontendEvent::scene_changed);
    producer->scene_submit_block.wait_until_entered();

    host.set_scene(rapid_scene_uuid(1), "Scene before pause");
    host.set_signal(recording_path, 11'000'000'000ULL, 60);
    host.fire_frontend(FrontendEvent::scene_changed);
    host.set_signal(
        recording_path,
        same_clock ? 11'000'000'000ULL : 11'016'666'667ULL,
        same_clock ? 60 : 61);
    host.fire_output(OutputEvent::paused);
    check(adapter.pending_scene_change_commands() == 1,
          "target scene was not waiting ahead of pause");
    check(adapter.pending_pause_resume_commands() == 1,
          "pause was not waiting behind target scene");

    producer->scene_submit_block.release();
    wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 1; },
                            "ordered pause was not submitted");
    wait_for([&] { return adapter.pending_pause_resume_commands() == 0; },
             "ordered pause did not become durable");
    check(scene_snapshots(producer).size() == 2,
          "scene accepted before pause was discarded or overtaken");
    check_no_simulated_writer_failure(
        producer,
        "scene-before-pause caused QPC/counter/paused writer failure");

    host.set_signal(recording_path, 14'000'000'000ULL, 248);
    host.fire_output(OutputEvent::stopped, 0);
    wait_for([&] { return adapter.state() == AdapterState::idle; },
             "scene-before-pause run did not stop successfully");
    check(accepted_kinds(producer) ==
              std::vector<std::string>({"scene_changed", "scene_changed", "pause", "stop"}),
          "global order did not preserve scene before pause/stop");
    check_no_simulated_writer_failure(producer, "scene-before-pause run failed writer order");
    const auto report = adapter.last_report();
    check(report.has_value() && report->successful,
          "scene-before-pause caused an unnecessary run abort");
    adapter.unload();
}

void test_scene_before_pause_global_order() {
    run_scene_before_pause_order_test(false);
    run_scene_before_pause_order_test(true);
}

void test_scene_before_resume_global_order() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->enforce_writer_order = true;
    producer->scene_submit_block.enabled = true;
    producer->scene_submit_block.one_shot = true;
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, ordered_test_options(root.path));
    check(adapter.load(), "scene-before-resume load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_scene(rapid_scene_uuid(0), "Worker blocker");
    host.set_signal(recording_path, 10'200'000'000ULL, 20);
    host.fire_frontend(FrontendEvent::scene_changed);
    producer->scene_submit_block.wait_until_entered();
    host.set_scene(rapid_scene_uuid(1), "Scene before pause and resume");
    host.set_signal(recording_path, 11'000'000'000ULL, 60);
    host.fire_frontend(FrontendEvent::scene_changed);
    host.set_signal(recording_path, 11'016'666'667ULL, 61);
    host.fire_output(OutputEvent::paused);
    host.set_signal(recording_path, 11'033'333'334ULL, 62);
    host.fire_output(OutputEvent::resumed);
    check(adapter.pending_pause_resume_commands() == 2,
          "pause/resume pair was not queued behind scene");

    producer->scene_submit_block.release();
    wait_for([&] {
        return adapter.state() == AdapterState::active &&
               adapter.pending_pause_resume_commands() == 0;
    }, "ordered pause/resume pair did not drain");
    check(scene_snapshots(producer).size() == 2,
          "scene before resume path was lost");
    check_no_simulated_writer_failure(
        producer,
        "scene-before-resume caused QPC/counter/paused writer failure");
    host.set_signal(recording_path, 14'000'000'000ULL, 248);
    host.fire_output(OutputEvent::stopped, 0);
    wait_for([&] { return adapter.state() == AdapterState::idle; },
             "scene-before-resume run did not stop successfully");
    check(accepted_kinds(producer) == std::vector<std::string>({
              "scene_changed", "scene_changed", "pause", "resume", "stop"}),
          "global order did not preserve scene/pause/resume sequence");
    check_no_simulated_writer_failure(producer, "scene-before-resume run failed writer order");
    adapter.unload();
}

void test_resume_before_scene_is_the_only_allowed_paused_order() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->enforce_writer_order = true;
    producer->scene_submit_block.enabled = true;
    producer->scene_submit_block.one_shot = true;
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, ordered_test_options(root.path));
    check(adapter.load(), "resume-before-scene load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_signal(recording_path, 11'000'000'000ULL, 68);
    host.fire_output(OutputEvent::paused);
    wait_for([&] { return adapter.pending_pause_resume_commands() == 0; },
             "resume-before-scene pause did not become durable");
    host.set_scene(rapid_scene_uuid(0), "Rejected while paused");
    host.set_signal(recording_path, 11'100'000'000ULL, 74);
    host.fire_frontend(FrontendEvent::scene_changed);

    host.set_signal(recording_path, 12'000'000'000ULL, 128);
    host.fire_output(OutputEvent::resumed);
    wait_for([&] { return adapter.pending_pause_resume_commands() == 0; },
             "resume did not become durable before allowed scene");
    wait_for([&] { return adapter.state() == AdapterState::active; },
             "durable resume did not reactivate scene admission");
    host.set_scene(rapid_scene_uuid(1), "Allowed after resume");
    bool scene_admitted = false;
    for (std::uint64_t attempt = 0; attempt != 100 && !scene_admitted; ++attempt) {
        host.set_signal(
            recording_path, 12'100'000'000ULL + attempt * 1'000'000ULL, 134 + attempt);
        host.fire_frontend(FrontendEvent::scene_changed);
        scene_admitted = host.has_log(
            "program scene change value snapshot queued for adapter worker");
        if (!scene_admitted) {
            std::this_thread::yield();
        }
    }
    check(scene_admitted, "post-resume scene could not be admitted on bounded callback path");
    {
        std::unique_lock lock(producer->scene_submit_block.mutex);
        if (!producer->scene_submit_block.changed.wait_for(
                lock, 2s, [&] { return producer->scene_submit_block.entered; })) {
            throw TestFailure(
                "post-resume scene did not reach worker blocking point; logs:\n" +
                host.joined_logs());
        }
    }
    host.set_signal(recording_path, 13'000'000'000ULL, 240);
    host.fire_output(OutputEvent::stopped, 0);
    producer->scene_submit_block.release();

    wait_for([&] { return adapter.state() == AdapterState::idle; },
             "resume/scene/stop sequence did not return idle");
    check(accepted_kinds(producer) ==
              std::vector<std::string>({"pause", "resume", "scene_changed", "stop"}),
          "paused/resume scene admission violated the allowed global order");
    check(scene_snapshots(producer).size() == 1 &&
              scene_snapshots(producer)[0].label == "Allowed after resume",
          "paused scene was invented or post-resume scene was lost");
    check_no_simulated_writer_failure(
        producer,
        "resume-before-scene caused QPC/counter/paused writer failure");
    adapter.unload();
}

void test_calibration_scene_control_global_order() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->enforce_writer_order = true;
    producer->scene_submit_block.enabled = true;
    producer->scene_submit_block.one_shot = true;
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, ordered_test_options(root.path));
    check(adapter.load(), "calibration-scene-control load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_scene(rapid_scene_uuid(0), "Worker blocker");
    host.set_signal(recording_path, 10'200'000'000ULL, 20);
    host.fire_frontend(FrontendEvent::scene_changed);
    producer->scene_submit_block.wait_until_entered();
    host.set_signal(recording_path, 12'100'000'000ULL, 134);
    host.fire_tick();
    host.set_scene(rapid_scene_uuid(1), "After calibration");
    host.set_signal(recording_path, 12'200'000'000ULL, 140);
    host.fire_frontend(FrontendEvent::scene_changed);
    host.set_signal(recording_path, 12'300'000'000ULL, 146);
    host.fire_output(OutputEvent::paused);

    producer->scene_submit_block.release();
    wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 1; },
                            "calibration/scene/pause commands did not drain");
    wait_for([&] { return adapter.pending_pause_resume_commands() == 0; },
             "calibration/scene pause did not become durable");
    check(accepted_kinds(producer) == std::vector<std::string>({
              "scene_changed", "calibration", "scene_changed", "pause"}),
          "calibration coalescing damaged global command order");
    check_no_simulated_writer_failure(
        producer,
        "calibration/scene/control caused QPC/counter/paused writer failure");
    host.set_signal(recording_path, 14'000'000'000ULL, 248);
    host.fire_output(OutputEvent::stopped, 0);
    wait_for([&] { return adapter.state() == AdapterState::idle; },
             "calibration-scene-control run did not stop successfully");
    check(accepted_kinds(producer) == std::vector<std::string>({
              "scene_changed", "calibration", "scene_changed", "pause", "stop"}),
          "stop overtook calibration/scene/control sequence");
    check_no_simulated_writer_failure(producer, "calibration global order failed");
    adapter.unload();
}

void test_scene_change_gates_and_active_value_snapshot() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    FakeFactory factory(producer);
    FakeHost host;
    std::mutex uuid_mutex;
    std::vector<std::thread::id> uuid_threads;
    unsigned uuid_index = 0;
    auto options = test_options(root.path);
    options.event_uuid_factory = [&] {
        std::lock_guard lock(uuid_mutex);
        uuid_threads.push_back(std::this_thread::get_id());
        return indexed_event_uuid(uuid_index++);
    };
    ObsJournalAdapter adapter(host, factory, std::move(options));
    check(adapter.load(), "scene-change load failed");

    host.fire_frontend(FrontendEvent::scene_changed);
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == 0,
          "scene outside recording touched OBS scene state");

    start_output(host, adapter, producer);
    host.fire_frontend(FrontendEvent::scene_changed);
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == 0,
          "scene before accepted recording_started touched OBS scene state");
    check(scene_snapshots(producer).empty(),
          "scene before accepted recording_started was journaled");

    anchor_recording(host, producer);
    host.fire_frontend(FrontendEvent::other);
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == 0,
          "non-program frontend event entered the scene change path");
    constexpr std::string_view source_uuid = "444eb885-e589-4338-832c-8f5fd7eaaf41";
    constexpr std::string_view exact_name = "Outro – Exakt";
    host.set_scene(source_uuid, exact_name);
    host.set_signal(recording_path, 10'500'000'000ULL, 38);
    const auto callback_thread = std::this_thread::get_id();
    host.fire_frontend(FrontendEvent::scene_changed);
    const auto scene_deadline = std::chrono::steady_clock::now() + 2s;
    while (scene_snapshots(producer).size() != 1 &&
           std::chrono::steady_clock::now() < scene_deadline) {
        std::this_thread::yield();
    }
    if (scene_snapshots(producer).size() != 1) {
        throw TestFailure("active scene_changed was not accepted exactly once; logs:\n" +
                          host.joined_logs());
    }

    const auto scenes = scene_snapshots(producer);
    check(scenes.size() == 1, "active scene change emitted more than one event");
    check(scenes[0].source_uuid == source_uuid, "stable scene UUID was not preserved");
    check(scenes[0].label == exact_name, "exact scene name was not preserved as label");
    check(!scenes[0].pair_id.has_value(), "scene_changed unexpectedly received pair_id");
    check(scenes[0].clock.monotonic_ns == 500'000'000ULL,
          "scene_changed relative QPC time changed");
    check(scenes[0].clock.output_frame_count == 38,
          "scene_changed output frame counter changed");
    check(!scenes[0].clock.recording_paused, "active scene_changed was marked paused");
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == 1 &&
              host.scene_release_calls.load(std::memory_order_acquire) == 1 &&
              host.scene_references.load(std::memory_order_acquire) == 0 &&
              host.scene_release_errors.load(std::memory_order_acquire) == 0,
          "successful scene reference was not released exactly once");
    {
        std::lock_guard lock(uuid_mutex);
        check(uuid_threads.size() == 2,
              "event UUID factory was not called once per recording/scene event");
        check(std::all_of(uuid_threads.begin(), uuid_threads.end(), [&](const auto id) {
                  return id != callback_thread;
              }),
              "event UUID was generated on the frontend callback thread");
    }

    stop_output_successfully(host, adapter, producer);
    adapter.unload();
}

void test_scene_change_fail_closed_capture_and_control_paths() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "scene-failure load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_signal(recording_path, 10'500'000'000ULL, 38);
    host.scene_available.store(false, std::memory_order_release);
    host.fire_frontend(FrontendEvent::scene_changed);
    check(host.scene_release_calls.load(std::memory_order_acquire) == 0,
          "null scene attempted to release a nonexistent reference");
    host.scene_available.store(true, std::memory_order_release);

    host.set_scene("", "Missing UUID");
    host.fire_frontend(FrontendEvent::scene_changed);
    host.set_scene("not-a-uuid", "Invalid UUID");
    host.fire_frontend(FrontendEvent::scene_changed);
    host.set_scene("444eb885-e589-4338-832c-8f5fd7eaaf41", "");
    host.fire_frontend(FrontendEvent::scene_changed);
    host.set_scene("444eb885-e589-4338-832c-8f5fd7eaaf41", "Clock failure");
    host.capture_clock_result.store(false, std::memory_order_release);
    host.fire_frontend(FrontendEvent::scene_changed);
    host.capture_clock_result.store(true, std::memory_order_release);
    host.set_scene("444eb885-e589-4338-832c-8f5fd7eaaf41", "Regressed clock");
    host.set_signal(recording_path, 9'999'999'999ULL, 6);
    host.fire_frontend(FrontendEvent::scene_changed);
    check(scene_snapshots(producer).empty(),
          "invalid scene identity or clock produced a scene_changed event");
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == 6 &&
              host.scene_release_calls.load(std::memory_order_acquire) == 5 &&
              host.scene_references.load(std::memory_order_acquire) == 0 &&
              host.scene_release_errors.load(std::memory_order_acquire) == 0,
          "scene failure paths did not release every acquired reference exactly once");

    host.set_scene("444eb885-e589-4338-832c-8f5fd7eaaf41", "Paused");
    host.set_signal(recording_path, 12'000'000'000ULL, 120);
    host.fire_output(OutputEvent::paused);
    wait_for([&] { return adapter.pending_pause_resume_commands() == 0; },
             "pause command did not become durable");
    const auto acquired_before_pause_scene =
        host.scene_acquire_calls.load(std::memory_order_acquire);
    host.fire_frontend(FrontendEvent::scene_changed);
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == acquired_before_pause_scene,
          "paused scene change touched OBS scene state");

    producer->durable_confirm_block.enabled = true;
    producer->durable_confirm_block.released = false;
    producer->durable_confirm_block.entered = false;
    host.set_signal(recording_path, 15'000'000'000ULL, 121);
    host.fire_output(OutputEvent::resumed);
    producer->durable_confirm_block.wait_until_entered();
    check(adapter.pending_pause_resume_commands() == 1,
          "resume transition was not visibly open");
    host.fire_frontend(FrontendEvent::scene_changed);
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == acquired_before_pause_scene,
          "scene change during open resume touched OBS scene state");
    producer->durable_confirm_block.release();
    wait_for([&] { return adapter.state() == AdapterState::active; },
             "resume did not reactivate after scene gate test");
    check(scene_snapshots(producer).empty(),
          "pause/open-control scene change produced an active event");

    stop_output_successfully(host, adapter, producer, 16'000'000'000ULL, 180);
    adapter.unload();
}

std::string rapid_scene_uuid(const unsigned index) {
    std::string value = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa0";
    constexpr char hex[] = "0123456789abcdef";
    value.back() = hex[index % 16U];
    return value;
}

void test_rapid_scene_changes_are_bounded_and_ordered() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->enforce_writer_order = true;
    producer->scene_submit_block.enabled = true;
    producer->scene_submit_block.one_shot = true;
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, ordered_test_options(root.path));
    check(adapter.load(), "rapid-scene load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_scene(rapid_scene_uuid(0), "Rapid-0");
    host.set_signal(recording_path, 10'100'000'000ULL, 14);
    host.fire_frontend(FrontendEvent::scene_changed);
    producer->scene_submit_block.wait_until_entered();
    for (unsigned index = 1; index <= scene_change_command_capacity + 3; ++index) {
        host.set_scene(rapid_scene_uuid(index), "Rapid-" + std::to_string(index));
        host.set_signal(recording_path, 10'100'000'000ULL + index * 1'000'000ULL, 14 + index);
        host.fire_frontend(FrontendEvent::scene_changed);
    }
    check(adapter.pending_scene_change_commands() == scene_change_command_capacity,
          "rapid scene queue was not capped at its declared capacity");
    check(host.has_log("bounded scene command queue is full"),
          "rapid scene overflow was not logged fail closed");
    check(host.scene_references.load(std::memory_order_acquire) == 0 &&
              host.scene_release_calls.load(std::memory_order_acquire) ==
                  scene_change_command_capacity + 4,
          "rapid scene callbacks retained an OBS scene reference");

    producer->scene_submit_block.release();
    wait_for([&] {
        return scene_snapshots(producer).size() == scene_change_command_capacity + 1;
    }, "bounded rapid scene queue did not drain");
    const auto scenes = scene_snapshots(producer);
    for (std::size_t index = 0; index < scenes.size(); ++index) {
        check(scenes[index].label == "Rapid-" + std::to_string(index),
              "rapid scene commands lost FIFO order");
        check(scenes[index].source_uuid == rapid_scene_uuid(static_cast<unsigned>(index)),
              "rapid scene UUID snapshot changed before worker processing");
    }
    check_no_simulated_writer_failure(producer, "rapid scene queue changed accepted order");
    stop_output_successfully(host, adapter, producer);
    check_no_simulated_writer_failure(producer, "rapid scene queue failed at stop");
    adapter.unload();
}

void test_stop_closes_queued_scene_change_path() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->enforce_writer_order = true;
    producer->scene_submit_block.enabled = true;
    producer->scene_submit_block.one_shot = true;
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, ordered_test_options(root.path));
    check(adapter.load(), "scene-stop load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_scene(rapid_scene_uuid(0), "Before stop");
    host.set_signal(recording_path, 10'500'000'000ULL, 38);
    host.fire_frontend(FrontendEvent::scene_changed);
    producer->scene_submit_block.wait_until_entered();
    host.set_scene(rapid_scene_uuid(1), "Also before stop");
    host.set_signal(recording_path, 10'600'000'000ULL, 44);
    host.fire_frontend(FrontendEvent::scene_changed);
    check(adapter.pending_scene_change_commands() == 1,
          "second scene was not queued before stop");
    host.set_signal(recording_path, 14'000'000'000ULL, 248);
    host.fire_output(OutputEvent::stopped, 0);
    const auto acquired_before_late_scene =
        host.scene_acquire_calls.load(std::memory_order_acquire);
    host.set_scene(rapid_scene_uuid(2), "Late after stop");
    host.fire_frontend(FrontendEvent::scene_changed);
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == acquired_before_late_scene,
          "scene callback linearized after stop touched OBS scene state");
    producer->scene_submit_block.release();
    wait_for_producer_count(producer, [](const auto& value) { return value.shutdowns == 1; },
                            "stop did not drain/close queued scene path");
    wait_for([&] { return adapter.state() == AdapterState::idle; },
             "scene-path stop did not return idle");
    const auto scenes = scene_snapshots(producer);
    check(scenes.size() == 2 && scenes[0].label == "Before stop" &&
              scenes[1].label == "Also before stop",
          "stop overtook a scene command linearized before its boundary");
    check(accepted_kinds(producer) ==
              std::vector<std::string>({"scene_changed", "scene_changed", "stop"}),
          "scene/stop global order was not deterministic");
    check_no_simulated_writer_failure(
        producer,
        "scene-before-stop caused QPC/counter/paused writer failure");
    check(adapter.pending_scene_change_commands() == 0,
          "stop left a queued scene command behind");
    check(host.scene_release_calls.load(std::memory_order_acquire) == 2 &&
              host.scene_references.load(std::memory_order_acquire) == 0,
          "stop path retained or double-released a scene reference");
    const auto report = adapter.last_report();
    check(report.has_value() && report->successful,
          "scene-before-stop caused an unnecessary run abort");
    adapter.unload();
}

void test_scene_stop_unload_and_closed_callback_gate_are_safe() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    auto module = std::make_shared<ModuleCounters>();
    FakeFactory factory(producer);
    FakeHost host;
    host.scene_acquire_block.enabled = true;
    auto options = test_options(root.path, module);
    options.unload_deadline = 1ms;
    ObsJournalAdapter adapter(host, factory, std::move(options));
    check(adapter.load(), "scene-unload load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);
    host.set_scene("444eb885-e589-4338-832c-8f5fd7eaaf41", "Blocked unload");
    host.set_signal(recording_path, 10'500'000'000ULL, 38);
    std::thread callback([&] { host.fire_frontend(FrontendEvent::scene_changed); });
    host.scene_acquire_block.wait_until_entered();
    adapter.unload();
    check(module->pins.load(std::memory_order_acquire) == 2,
          "scene callback/worker DLL pins were released during unload timeout");
    check(host.release_calls.load(std::memory_order_acquire) == 0,
          "recording output reference was released while scene clock capture was blocked");
    host.scene_acquire_block.release();
    callback.join();
    wait_for([&] { return module->pins.load(std::memory_order_acquire) == 0; },
             "scene callback/worker pins did not drain after unload");
    check(host.scene_release_calls.load(std::memory_order_acquire) == 1 &&
              host.scene_references.load(std::memory_order_acquire) == 0 &&
              host.scene_release_errors.load(std::memory_order_acquire) == 0,
          "blocked unload did not release scene reference exactly once");
    check(host.disconnect_calls.load(std::memory_order_acquire) == 1 &&
              host.release_calls.load(std::memory_order_acquire) == 1,
          "scene unload did not disconnect and release output exactly once");
    const auto scene_acquires = host.scene_acquire_calls.load(std::memory_order_acquire);
    const auto log_count = host.logs.size();
    host.fire_stale_frontend(FrontendEvent::scene_changed);
    check(host.scene_acquire_calls.load(std::memory_order_acquire) == scene_acquires &&
              host.logs.size() == log_count,
          "callback after closed gate accessed adapter or OBS host state");
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

    host.set_signal(recording_path, 15'000'000'000ULL, 169);
    producer->durable_confirm_block.enabled = true;
    host.fire_output(OutputEvent::resumed);
    wait_for_producer_count(producer, [](const auto& value) { return value.resumes == 1; },
                            "actual output unpause did not submit one resume snapshot");
    producer->durable_confirm_block.wait_until_entered();
    check(adapter.state() == AdapterState::paused,
          "queue acceptance reactivated adapter before durable resume");
    check(adapter.pending_pause_resume_commands() == 1,
          "resume pending counter cleared before durable confirmation");
    check(host.output_connected, "stop signal disconnected while valid resume was pending");
    host.set_signal(recording_path, 16'000'000'000ULL, 180);
    host.fire_tick();
    {
        std::lock_guard lock(producer->mutex);
        check(producer->calibrations == 0,
              "tick was accepted before resume became durable");
    }
    producer->durable_confirm_block.release();
    wait_for([&] { return adapter.state() == AdapterState::active; },
             "resume did not reactivate adapter");
    check(adapter.pending_pause_resume_commands() == 0,
          "resume pending counter did not return to zero");
    host.set_signal(recording_path, 17'100'000'000ULL, 248);
    host.fire_tick();
    wait_for_producer_count(producer, [](const auto& value) { return value.calibrations == 1; },
                            "calibration did not continue after resume");
    stop_output_successfully(host, adapter, producer, 18'000'000'000ULL, 302);
    std::lock_guard lock(producer->mutex);
    check(!producer->stop.clock.recording_paused, "stop after resume retained paused flag");
}

void test_writer_terminal_resume_is_immediate_visible_cleanup() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "terminal-resume load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);
    producer->durable_confirm_block.enabled = true;
    producer->durable_confirm_block.one_shot = true;
    host.set_signal(recording_path, 12'000'000'000ULL, 120);
    host.fire_output(OutputEvent::paused);
    wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 1; },
                            "pause did not precede terminal resume");
    producer->durable_confirm_block.wait_until_entered();
    {
        std::lock_guard lock(producer->mutex);
        producer->durable_result = ProducerResult::producer_internal_error;
        producer->fail_durable_confirm_call = 2;
    }
    host.set_signal(recording_path, 15'000'000'000ULL, 124);
    host.fire_output(OutputEvent::resumed);
    check(adapter.pending_pause_resume_commands() == 2,
          "terminal resume was not queued behind the blocked pause");
    producer->durable_confirm_block.release();
    wait_for([&] { return adapter.state() == AdapterState::failed; },
             "writer-terminal resume was not immediately fail closed");
    check(host.disconnect_calls.load(std::memory_order_acquire) == 1,
          "writer-terminal cleanup did not disconnect signals exactly once");
    check(host.release_calls.load(std::memory_order_acquire) == 1,
          "writer-terminal cleanup did not release output exactly once");
    check(!host.output_connected, "writer-terminal cleanup left output signals connected");
    host.fire_output(OutputEvent::stopped, 0);
    {
        std::lock_guard lock(producer->mutex);
        check(producer->stops == 0, "post-cleanup stop reached failed producer");
        check(producer->shutdowns == 1, "writer-terminal cleanup did not shut down once");
    }
    wait_for([&] { return host.has_log("resume was not durably written"); },
             "writer-terminal cleanup reason was not logged");
    check(host.has_log("fail-closed cleanup entered"),
          "fail-closed cleanup entry was not logged");
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

void test_ordered_pause_resume_pause_survives_a_blocked_worker() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->pause_submit_block.enabled = true;
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "rapid pause sequence load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_signal(recording_path, 12'000'000'000ULL, 120);
    host.fire_output(OutputEvent::paused);
    producer->pause_submit_block.wait_until_entered();
    host.set_signal(recording_path, 13'000'000'000ULL, 121);
    host.fire_output(OutputEvent::resumed);
    host.set_signal(recording_path, 14'000'000'000ULL, 122);
    host.fire_output(OutputEvent::paused);
    producer->pause_submit_block.release();

    wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 2; },
                            "second ordered pause was not drained");
    wait_for_producer_count(producer, [](const auto& value) { return value.resumes == 1; },
                            "ordered resume was not drained");
    check(adapter.state() == AdapterState::paused,
          "latest actual pause state was not retained after worker drain");
    host.set_signal(recording_path, 15'000'000'000ULL, 122);
    host.fire_output(OutputEvent::stopped, 0);
    wait_for([&] { return adapter.state() == AdapterState::idle; },
             "stop after ordered rapid pause sequence failed");
}

void test_forced_failure_wins_over_an_already_queued_stop() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->pause_submit_block.enabled = true;
    FakeFactory factory(producer);
    FakeHost host;
    auto options = test_options(root.path);
    auto pause_callbacks = std::make_shared<std::atomic<unsigned>>(0);
    options.callback_probe = [pause_callbacks](const CallbackKind kind) {
        if (kind == CallbackKind::output_pause &&
            pause_callbacks->fetch_add(1, std::memory_order_acq_rel) != 0) {
            throw std::runtime_error("injected fail-closed pause callback");
        }
    };
    ObsJournalAdapter adapter(host, factory, std::move(options));
    check(adapter.load(), "forced-vs-stop load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_signal(recording_path, 12'000'000'000ULL, 120);
    host.fire_output(OutputEvent::paused);
    producer->pause_submit_block.wait_until_entered();
    host.set_signal(recording_path, 15'000'000'000ULL, 121);
    host.fire_output(OutputEvent::stopped, 0);
    host.fire_output(OutputEvent::paused);
    producer->pause_submit_block.release();

    wait_for([&] { return adapter.state() == AdapterState::failed; },
             "forced failure did not win over queued stop");
    std::lock_guard lock(producer->mutex);
    check(producer->stops == 0, "queued stop was authorized after fail-closed had won");
    check(producer->shutdowns == 1, "forced failure did not shut the producer down exactly once");
}

void test_failure_from_a_draining_callback_wins_over_stop_authorization() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "draining-failure-vs-stop load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.capture_clock_block.enabled = true;
    std::thread pause([&] { host.fire_output(OutputEvent::paused); });
    host.capture_clock_block.wait_until_entered();
    host.set_signal(recording_path, 14'000'000'000ULL, 248);
    host.fire_output(OutputEvent::stopped, 0);
    host.capture_result.store(false, std::memory_order_release);
    host.capture_clock_block.release();
    pause.join();

    wait_for([&] { return adapter.state() == AdapterState::failed; },
             "draining callback failure did not defeat stop authorization");
    std::lock_guard lock(producer->mutex);
    check(producer->stops == 0, "stop was authorized after a draining callback failed");
    check(producer->shutdowns == 1, "draining callback failure did not shut down once");
}

void test_active_stop_retains_counter_quantized_qpc() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "quantized-stop load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);
    host.set_signal(recording_path, 14'100'000'000ULL, 248);
    host.fire_output(OutputEvent::stopped, 0);
    wait_for([&] { return adapter.state() == AdapterState::idle; },
             "active quantized stop failed");
    std::lock_guard lock(producer->mutex);
    check(producer->stop.clock.monotonic_ns == 4'000'000'000ULL,
          "active stop used callback latency instead of the output counter anchor");
}

void test_control_fifo_overflow_is_fail_closed() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    producer->pause_submit_block.enabled = true;
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "control-overflow load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.set_signal(recording_path, 12'000'000'000ULL, 120);
    host.fire_output(OutputEvent::paused);
    producer->pause_submit_block.wait_until_entered();
    for (const auto event : {OutputEvent::resumed, OutputEvent::paused, OutputEvent::resumed,
                             OutputEvent::paused, OutputEvent::resumed}) {
        host.fire_output(event);
    }
    producer->pause_submit_block.release();

    wait_for([&] { return adapter.state() == AdapterState::failed; },
             "full control FIFO did not fail closed");
    std::lock_guard lock(producer->mutex);
    check(producer->stops == 0, "control FIFO overflow authorized a stop");
    check(producer->shutdowns == 1, "control FIFO overflow did not shut down exactly once");
}

void test_stop_wins_over_blocked_pause_and_resume_callbacks() {
    for (const auto event : {OutputEvent::paused, OutputEvent::resumed}) {
        TempRoot root;
        auto producer = std::make_shared<ProducerState>();
        FakeFactory factory(producer);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options(root.path));
        check(adapter.load(), "blocked control-vs-stop load failed");
        start_output(host, adapter, producer);
        anchor_recording(host, producer);
        if (event == OutputEvent::resumed) {
            host.set_signal(recording_path, 12'000'000'000ULL, 120);
            host.fire_output(OutputEvent::paused);
            wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 1; },
                                    "pause was not accepted before resume-vs-stop race");
        }

        host.capture_clock_block.enabled = true;
        std::thread callback([&] { host.fire_output(event); });
        host.capture_clock_block.wait_until_entered();
        host.set_signal(
            recording_path,
            event == OutputEvent::paused ? 14'000'000'000ULL : 15'000'000'000ULL,
            event == OutputEvent::paused ? 248 : 121);
        host.fire_output(OutputEvent::stopped, 0);
        host.capture_clock_block.release();
        callback.join();
        wait_for([&] { return adapter.state() == AdapterState::idle; },
                 "stop did not win over blocked pause/resume callback");
        std::lock_guard lock(producer->mutex);
        check(producer->resumes == 0, "blocked resume overtook stop");
        check(producer->pauses == (event == OutputEvent::resumed ? 1U : 0U),
              "blocked pause overtook stop");
        check(producer->stop.clock.recording_paused == (event == OutputEvent::resumed),
              "stop did not retain the state that won before the blocked control signal");
    }
}

void test_pause_linearization_suppresses_a_tick_still_capturing() {
    TempRoot root;
    auto producer = std::make_shared<ProducerState>();
    FakeFactory factory(producer);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options(root.path));
    check(adapter.load(), "pause-vs-tick load failed");
    start_output(host, adapter, producer);
    anchor_recording(host, producer);

    host.capture_clock_block.enabled = true;
    host.capture_clock_block.one_shot = true;
    std::thread tick([&] { host.fire_tick(); });
    host.capture_clock_block.wait_until_entered();
    host.set_signal(recording_path, 12'000'000'000ULL, 120);
    host.fire_output(OutputEvent::paused);
    host.capture_clock_block.release();
    tick.join();
    wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 1; },
                            "pause was not accepted in tick race");
    {
        std::lock_guard lock(producer->mutex);
        check(producer->calibrations == 0, "tick captured before pause was written after pause");
    }
    host.set_signal(recording_path, 15'000'000'000ULL, 120);
    host.fire_output(OutputEvent::stopped, 0);
    wait_for([&] { return adapter.state() == AdapterState::idle; },
             "paused stop after tick race failed");
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

enum class BlockedScenario {
    idle_frontend,
    start_pending,
    active_tick,
    output_pause,
    output_resume,
    output_stop
};

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

    if (scenario == BlockedScenario::active_tick || scenario == BlockedScenario::output_pause ||
        scenario == BlockedScenario::output_resume || scenario == BlockedScenario::output_stop) {
        start_output(host, adapter, producer);
        anchor_recording(host, producer);
    }

    if (scenario == BlockedScenario::output_resume) {
        host.set_signal(recording_path, 12'000'000'000ULL, 120);
        host.fire_output(OutputEvent::paused);
        wait_for_producer_count(producer, [](const auto& value) { return value.pauses == 1; },
                                "pause was not accepted before blocked resume");
    }

    if (scenario == BlockedScenario::active_tick) {
        callback_block->enabled = true;
    }
    if (scenario == BlockedScenario::output_stop) {
        host.capture_output_block.enabled = true;
    } else if (scenario == BlockedScenario::output_pause ||
               scenario == BlockedScenario::output_resume) {
        host.capture_clock_block.enabled = true;
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
    } else if (scenario == BlockedScenario::output_stop) {
        active_block = &host.capture_output_block;
        host.set_signal(recording_path, 14'000'000'000ULL, 248);
        callback = std::thread([&] { host.fire_output(OutputEvent::stopped, 0); });
    } else {
        active_block = &host.capture_clock_block;
        const auto event = scenario == BlockedScenario::output_pause ? OutputEvent::paused
                                                                     : OutputEvent::resumed;
        host.set_signal(
            recording_path,
            scenario == BlockedScenario::output_pause ? 12'000'000'000ULL : 14'000'000'000ULL,
            scenario == BlockedScenario::output_pause ? 120 : 121);
        callback = std::thread([&] { host.fire_output(event, 0); });
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
    run_blocked_unload_scenario(BlockedScenario::output_pause);
    run_blocked_unload_scenario(BlockedScenario::output_resume);
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
                            CallbackKind::output_start, CallbackKind::output_pause,
                            CallbackKind::output_resume, CallbackKind::output_stop}) {
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
            } else if (kind == CallbackKind::output_pause) {
                host.fire_output(OutputEvent::paused, 0);
            } else if (kind == CallbackKind::output_resume) {
                host.fire_output(OutputEvent::resumed, 0);
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
        {"scene-change/gates/value-snapshot/worker-uuid/reference",
         test_scene_change_gates_and_active_value_snapshot},
        {"scene-change/fail-closed-capture/pause-control/reference",
         test_scene_change_fail_closed_capture_and_control_paths},
        {"global-order/scene-before-pause/same-and-next-frame",
         test_scene_before_pause_global_order},
        {"global-order/scene-before-pause-resume", test_scene_before_resume_global_order},
        {"global-order/paused-resume-before-scene-stop",
         test_resume_before_scene_is_the_only_allowed_paused_order},
        {"global-order/calibration-scene-pause-stop",
         test_calibration_scene_control_global_order},
        {"scene-change/rapid-bounded-fifo", test_rapid_scene_changes_are_bounded_and_ordered},
        {"global-order/scene-before-stop/stop-before-late-scene",
         test_stop_closes_queued_scene_change_path},
        {"scene-change/unload/callback-gate/module-lifetime",
         test_scene_stop_unload_and_closed_callback_gate_are_safe},
        {"output-authority/path/session/calibration/stop/cleanup/idempotence",
         test_output_lifecycle_authority_normative_path_and_exact_binding},
        {"failed-or-missing-output-stop", test_failed_or_missing_output_stop_never_calls_normal_stop},
        {"actual-output-pause-resume/calibration-gate/order",
         test_actual_output_pause_resume_is_ordered_and_gates_calibration},
        {"writer-terminal-resume/immediate-visible-cleanup",
         test_writer_terminal_resume_is_immediate_visible_cleanup},
        {"pause-sequence-failure/stop-while-paused", test_pause_sequence_failure_and_stop_while_paused},
        {"ordered-pause-resume-pause/blocked-worker",
         test_ordered_pause_resume_pause_survives_a_blocked_worker},
        {"forced-failure-wins-over-queued-stop",
         test_forced_failure_wins_over_an_already_queued_stop},
        {"draining-callback-failure-wins-over-stop",
         test_failure_from_a_draining_callback_wins_over_stop_authorization},
        {"active-stop/counter-quantized-qpc", test_active_stop_retains_counter_quantized_qpc},
        {"control-fifo-overflow/fail-closed", test_control_fifo_overflow_is_fail_closed},
        {"stop-wins-over-blocked-pause-resume",
         test_stop_wins_over_blocked_pause_and_resume_callbacks},
        {"pause-linearization-suppresses-capturing-tick",
         test_pause_linearization_suppresses_a_tick_still_capturing},
        {"invalid-session/localappdata/unsupported/concurrent-start",
         test_invalid_session_localappdata_and_unsupported_or_concurrent_start_fail_closed},
        {"unload-blocked-idle/start-pending/active-tick/output-pause/resume/stop",
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
