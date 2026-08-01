#include "matrix_auto_cutter/obs_adapter.hpp"

#include <Windows.h>

#include <algorithm>
#include <cctype>
#include <exception>
#include <utility>

namespace matrix_auto_cutter::obs_adapter {
namespace {

constexpr auto callback_drain_deadline = std::chrono::seconds(1);
constexpr auto worker_unload_deadline = producer_shutdown_deadline + std::chrono::seconds(2);
constexpr std::uint64_t nominal_video_fps = 60;
constexpr std::uint64_t nanoseconds_per_second = 1'000'000'000ULL;

std::optional<std::uint64_t> frame_span_ns(const std::uint64_t frames) noexcept {
    constexpr auto whole_frame_limit = UINT64_MAX / nanoseconds_per_second;
    const auto whole_seconds = frames / nominal_video_fps;
    if (whole_seconds > whole_frame_limit) {
        return std::nullopt;
    }
    return whole_seconds * nanoseconds_per_second +
           ((frames % nominal_video_fps) * nanoseconds_per_second) / nominal_video_fps;
}

bool direct_mp4_path(const std::string_view path) noexcept {
    if (path.size() < 5) {
        return false;
    }
    constexpr std::string_view suffix = ".mp4";
    const auto tail = path.substr(path.size() - suffix.size());
    for (std::size_t index = 0; index < suffix.size(); ++index) {
        const auto value = static_cast<unsigned char>(tail[index]);
        if (static_cast<char>(std::tolower(value)) != suffix[index]) {
            return false;
        }
    }
    return true;
}

bool direct_mp4_signal(const RecordingSignal& signal) noexcept {
    return direct_mp4_path(signal.path.view()) && signal.output_id.view() == "ffmpeg_muxer" &&
           !signal.fragmented_mp4;
}

std::optional<std::filesystem::path> path_from_utf8(const std::string_view value) noexcept {
    try {
        if (value.empty() || value.size() > static_cast<std::size_t>(INT_MAX)) {
            return std::nullopt;
        }
        const auto input_size = static_cast<int>(value.size());
        const int count = MultiByteToWideChar(
            CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), input_size, nullptr, 0);
        if (count <= 0) {
            return std::nullopt;
        }
        std::wstring wide(static_cast<std::size_t>(count), L'\0');
        if (MultiByteToWideChar(
                CP_UTF8,
                MB_ERR_INVALID_CHARS,
                value.data(),
                input_size,
                wide.data(),
                count) != count) {
            return std::nullopt;
        }
        return std::filesystem::path(std::move(wide));
    } catch (...) {
        return std::nullopt;
    }
}

std::string path_to_utf8(const std::filesystem::path& path) noexcept {
    try {
        const std::wstring wide = path.native();
        if (wide.empty() || wide.size() > static_cast<std::size_t>(INT_MAX)) {
            return {};
        }
        const auto input_size = static_cast<int>(wide.size());
        const int count = WideCharToMultiByte(
            CP_UTF8, WC_ERR_INVALID_CHARS, wide.data(), input_size, nullptr, 0, nullptr, nullptr);
        if (count <= 0) {
            return {};
        }
        std::string utf8(static_cast<std::size_t>(count), '\0');
        if (WideCharToMultiByte(
                CP_UTF8,
                WC_ERR_INVALID_CHARS,
                wide.data(),
                input_size,
                utf8.data(),
                count,
                nullptr,
                nullptr) != count) {
            return {};
        }
        return utf8;
    } catch (...) {
        return {};
    }
}

std::optional<std::filesystem::path> journal_path_for(
    const std::string_view recording_path,
    const std::string_view journal_id) noexcept {
    const auto recording = path_from_utf8(recording_path);
    if (!recording.has_value() || journal_id.size() != 36) {
        return std::nullopt;
    }
    try {
        std::wstring suffix = L".matrix-";
        suffix.append(journal_id.begin(), journal_id.end());
        suffix += L".recording-journal.ndjson";
        return std::filesystem::path(recording->native() + suffix);
    } catch (...) {
        return std::nullopt;
    }
}

}  // namespace

bool BoundedPath::assign(const std::string_view value) noexcept {
    if (value.empty() || value.size() > max_recording_path_utf8 ||
        value.find('\0') != std::string_view::npos) {
        size = 0;
        bytes[0] = '\0';
        return false;
    }
    std::copy(value.begin(), value.end(), bytes.begin());
    size = value.size();
    bytes[size] = '\0';
    return true;
}

std::string_view BoundedPath::view() const noexcept {
    return size <= max_recording_path_utf8 ? std::string_view(bytes.data(), size)
                                           : std::string_view{};
}

ObsJournalAdapter::ObsJournalAdapter(
    AdapterHost& host,
    ProducerFactory& factory,
    AdapterOptions options)
    : host_(host), factory_(factory), options_(std::move(options)) {}

ObsJournalAdapter::~ObsJournalAdapter() { unload(); }

bool ObsJournalAdapter::load() noexcept {
    bool expected = false;
    if (!loaded_.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) {
        return true;
    }
    try {
        std::unique_ptr<WorkerThreadLifetime> lifetime;
        if (options_.worker_lifetime_factory) {
            lifetime = options_.worker_lifetime_factory();
            if (!lifetime) {
                loaded_.store(false, std::memory_order_release);
                return false;
            }
        }
        worker_ = std::thread([this, lifetime = std::move(lifetime)]() mutable noexcept {
            worker_main();
            if (lifetime) {
                lifetime->exit_thread();
            }
        });
        if (!host_.install_callbacks(frontend_boundary, tick_boundary, this)) {
            {
                std::lock_guard lock(command_mutex_);
                unload_requested_ = true;
            }
            command_changed_.notify_one();
            worker_.join();
            loaded_.store(false, std::memory_order_release);
            return false;
        }
        state_.store(AdapterState::idle, std::memory_order_release);
        host_.log(LogLevel::info, "module loaded: Matrix Auto Cutter OBS Journal Adapter 0.1.0-experimental");
        return true;
    } catch (...) {
        loaded_.store(false, std::memory_order_release);
        state_.store(AdapterState::unloaded, std::memory_order_release);
        return false;
    }
}

void ObsJournalAdapter::unload() noexcept {
    if (!loaded_.exchange(false, std::memory_order_acq_rel)) {
        return;
    }
    try {
        host_.remove_callbacks();
        accepting_snapshots_.store(false, std::memory_order_release);
        state_.store(AdapterState::unloading, std::memory_order_release);
        {
            std::lock_guard lock(command_mutex_);
            unload_requested_ = true;
            forced_shutdown_.store(true, std::memory_order_release);
        }
        command_changed_.notify_one();
        bool done = false;
        {
            std::unique_lock lock(worker_done_mutex_);
            done = worker_done_changed_.wait_for(lock, worker_unload_deadline, [&] {
                return worker_done_;
            });
        }
        if (worker_.joinable()) {
            if (done) {
                worker_.join();
            } else {
                worker_.detach();
                host_.log(
                    LogLevel::error,
                    "adapter worker unload deadline exceeded; DLL remains pinned until thread exit");
            }
        }
        if (done) {
            state_.store(AdapterState::unloaded, std::memory_order_release);
            host_.log(LogLevel::info, "module callbacks and native resources released");
        }
    } catch (...) {
        if (worker_.joinable()) {
            worker_.detach();
        }
    }
}

AdapterState ObsJournalAdapter::state() const noexcept {
    return state_.load(std::memory_order_acquire);
}

std::optional<RunReport> ObsJournalAdapter::last_report() const {
    std::lock_guard lock(report_mutex_);
    return last_report_;
}

void ObsJournalAdapter::frontend_boundary(const FrontendEvent event, void* private_data) noexcept {
    try {
        if (private_data != nullptr) {
            static_cast<ObsJournalAdapter*>(private_data)->on_frontend(event);
        }
    } catch (...) {
    }
}

void ObsJournalAdapter::tick_boundary(void* private_data) noexcept {
    try {
        if (private_data != nullptr) {
            static_cast<ObsJournalAdapter*>(private_data)->on_tick();
        }
    } catch (...) {
    }
}

void ObsJournalAdapter::finish_callback() noexcept {
    if (callbacks_in_flight_.fetch_sub(1, std::memory_order_acq_rel) == 1) {
        callback_finished_.notify_all();
    }
}

void ObsJournalAdapter::on_frontend(const FrontendEvent event) noexcept {
    callbacks_in_flight_.fetch_add(1, std::memory_order_acq_rel);
    struct Guard final {
        ObsJournalAdapter* self;
        ~Guard() { self->finish_callback(); }
    } guard{this};

    if (!loaded_.load(std::memory_order_acquire)) {
        return;
    }
    if (event == FrontendEvent::recording_started) {
        RecordingSignal signal;
        if (!host_.acquire_recording_start(signal)) {
            host_.log(LogLevel::error, "recording start rejected: no safe current recording output/path");
            return;
        }
        if (!direct_mp4_signal(signal)) {
            host_.release_recording_output();
            host_.log(
                LogLevel::warning,
                "recording start rejected fail closed: output is not traditional Direct MP4");
            return;
        }
        std::unique_lock lock(command_mutex_, std::try_to_lock);
        auto expected = AdapterState::idle;
        if (!lock.owns_lock() || !state_.compare_exchange_strong(
                                    expected,
                                    AdapterState::start_pending,
                                    std::memory_order_acq_rel,
                                    std::memory_order_acquire)) {
            host_.release_recording_output();
            host_.log(LogLevel::warning, "concurrent recording start rejected fail closed");
            return;
        }
        pending_start_ = signal;
        lock.unlock();
        command_changed_.notify_one();
        host_.log(LogLevel::info, "confirmed recording start captured; producer start queued");
        return;
    }
    if (event != FrontendEvent::recording_stopped) {
        return;
    }
    const auto current = state_.load(std::memory_order_acquire);
    if (current == AdapterState::idle || current == AdapterState::unloaded ||
        current == AdapterState::unloading) {
        return;
    }
    accepting_snapshots_.store(false, std::memory_order_release);
    RecordingSignal signal;
    bool captured = host_.capture_recording_stop(signal);
    if (captured) {
        const auto started_frames =
            recording_started_frame_count_.load(std::memory_order_acquire);
        const auto started_ns = recording_started_ns_.load(std::memory_order_acquire);
        constexpr std::uint64_t max_stop_qpc_adjustment_frames = 8;
        const auto max_adjustment = frame_span_ns(max_stop_qpc_adjustment_frames);
        const auto counter_span = signal.output_frame_count >= started_frames
                                      ? frame_span_ns(signal.output_frame_count - started_frames)
                                      : std::nullopt;
        if (!recording_started_accepted_.load(std::memory_order_acquire) ||
            !max_adjustment.has_value() || !counter_span.has_value() ||
            started_ns > UINT64_MAX - *counter_span) {
            captured = false;
        } else {
            const auto final_frame_ns = started_ns + *counter_span;
            const auto difference = final_frame_ns <= signal.absolute_monotonic_ns
                                        ? signal.absolute_monotonic_ns - final_frame_ns
                                        : final_frame_ns - signal.absolute_monotonic_ns;
            if (difference > *max_adjustment) {
                captured = false;
            } else {
                signal.absolute_monotonic_ns = final_frame_ns;
            }
        }
    }
    std::unique_lock lock(command_mutex_, std::try_to_lock);
    if (!captured || !lock.owns_lock()) {
        if (lock.owns_lock()) {
            forced_shutdown_.store(true, std::memory_order_release);
            lock.unlock();
        } else {
            forced_shutdown_.store(true, std::memory_order_release);
        }
        state_.store(AdapterState::failed, std::memory_order_release);
        command_changed_.notify_one();
        host_.log(LogLevel::error, "recording stop snapshot failed; run forced fail closed");
        return;
    }
    pending_stop_ = signal;
    state_.store(AdapterState::stopping, std::memory_order_release);
    lock.unlock();
    command_changed_.notify_one();
    host_.log(LogLevel::info, "confirmed recording stop captured");
}

void ObsJournalAdapter::on_tick() noexcept {
    callbacks_in_flight_.fetch_add(1, std::memory_order_acq_rel);
    struct Guard final {
        ObsJournalAdapter* self;
        ~Guard() { self->finish_callback(); }
    } guard{this};
    if (state_.load(std::memory_order_acquire) != AdapterState::active ||
        !accepting_snapshots_.load(std::memory_order_acquire) || producer_ == nullptr) {
        return;
    }
    std::uint64_t absolute = 0;
    std::uint64_t frames = 0;
    if (!host_.capture_clock(absolute, frames)) {
        accepting_snapshots_.store(false, std::memory_order_release);
        host_.log(LogLevel::error, "calibration capture failed; snapshots disabled");
        return;
    }
    const auto origin = origin_ns_.load(std::memory_order_acquire);
    if (absolute < origin) {
        accepting_snapshots_.store(false, std::memory_order_release);
        host_.log(LogLevel::error, "non-monotone QPC observation; snapshots disabled");
        return;
    }
    const auto relative = absolute - origin;
    if (!recording_started_claimed_.load(std::memory_order_acquire)) {
        const auto initial_frames = initial_frame_count_.load(std::memory_order_acquire);
        if (frames <= initial_frames) {
            return;
        }
        const auto frame_elapsed = frame_span_ns(frames - initial_frames);
        if (!frame_elapsed.has_value() || relative < *frame_elapsed) {
            accepting_snapshots_.store(false, std::memory_order_release);
            host_.log(LogLevel::error, "output frame/QPC anchor is invalid; run failed closed");
            return;
        }
        bool expected = false;
        if (!recording_started_claimed_.compare_exchange_strong(
                expected, true, std::memory_order_acq_rel, std::memory_order_acquire)) {
            return;
        }
        if (!pending_recording_started_.has_value()) {
            accepting_snapshots_.store(false, std::memory_order_release);
            host_.log(LogLevel::error, "recording_started snapshot was not prepared");
            return;
        }
        if (initial_frames == UINT64_MAX) {
            accepting_snapshots_.store(false, std::memory_order_release);
            host_.log(LogLevel::error, "initial output counter cannot be advanced");
            return;
        }
        pending_recording_started_->clock =
            ClockSnapshot{relative - *frame_elapsed, initial_frames + 1, false};
        const auto started = producer_->submit(std::move(*pending_recording_started_));
        if (started != CallbackResult::accepted) {
            accepting_snapshots_.store(false, std::memory_order_release);
            host_.log(LogLevel::error, "recording_started event rejected; run failed closed");
            return;
        }
        recording_started_ns_.store(
            origin + pending_recording_started_->clock.monotonic_ns,
            std::memory_order_relaxed);
        recording_started_frame_count_.store(
            pending_recording_started_->clock.output_frame_count,
            std::memory_order_release);
        recording_started_accepted_.store(true, std::memory_order_release);
        next_calibration_ns_.store(
            relative - *frame_elapsed +
                static_cast<std::uint64_t>(calibration_interval.count()) *
                    nanoseconds_per_second,
            std::memory_order_release);
        host_.log(
            LogLevel::info,
            "actual output-frame clock anchored; exactly one recording_started accepted");
        return;
    }
    if (!recording_started_accepted_.load(std::memory_order_acquire)) {
        return;
    }
    auto due = next_calibration_ns_.load(std::memory_order_acquire);
    if (relative < due || !next_calibration_ns_.compare_exchange_strong(
                              due,
                              relative + static_cast<std::uint64_t>(calibration_interval.count()) *
                                             nanoseconds_per_second,
                              std::memory_order_acq_rel,
                              std::memory_order_acquire)) {
        return;
    }
    const auto outcome = producer_->submit(
        CalibrationSnapshot{ClockSnapshot{relative, frames, false}});
    if (outcome == CallbackResult::accepted) {
        calibration_count_.fetch_add(1, std::memory_order_relaxed);
        host_.log(LogLevel::info, "calibration snapshot accepted");
    } else {
        accepting_snapshots_.store(false, std::memory_order_release);
        host_.log(LogLevel::error, "producer rejected calibration; run cannot be successful");
    }
}

bool ObsJournalAdapter::wait_for_callbacks() noexcept {
    try {
        std::unique_lock lock(callback_wait_mutex_);
        return callback_finished_.wait_for(lock, callback_drain_deadline, [&] {
            return callbacks_in_flight_.load(std::memory_order_acquire) == 0;
        });
    } catch (...) {
        return false;
    }
}

void ObsJournalAdapter::process_start(const RecordingSignal& signal) noexcept {
    const std::string recording(signal.path.view());
    const std::string journal_id = options_.uuid_factory ? options_.uuid_factory() : std::string{};
    const auto journal = journal_path_for(recording, journal_id);
    if (!journal.has_value()) {
        fail_current_run(ProducerResult::producer_internal_error, "journal path creation failed");
        return;
    }
    auto producer = factory_.create();
    if (!producer) {
        fail_current_run(ProducerResult::producer_internal_error, "producer allocation failed");
        return;
    }
    const RecordingStart start{
        *journal,
        recording,
        options_.producer_version,
        std::string(host_.obs_version()),
    };
    const auto started = producer->start_recording(start);
    if (started != ProducerResult::producer_ok) {
        static_cast<void>(producer->shutdown());
        producer_ = std::move(producer);
        journal_path_ = *journal;
        recording_path_ = recording;
        fail_current_run(started, "native producer start failed");
        return;
    }
    const std::string event_id = options_.uuid_factory ? options_.uuid_factory() : std::string{};
    EventSnapshot pending_started{
        event_id,
        EventType::recording_started,
        ClockSnapshot{0, signal.output_frame_count, false},
        std::nullopt,
        std::nullopt,
        std::nullopt,
    };
    producer_ = std::move(producer);
    pending_recording_started_ = std::move(pending_started);
    journal_path_ = *journal;
    recording_path_ = recording;
    origin_ns_.store(signal.absolute_monotonic_ns, std::memory_order_release);
    initial_frame_count_.store(signal.output_frame_count, std::memory_order_release);
    recording_started_ns_.store(0, std::memory_order_relaxed);
    recording_started_frame_count_.store(0, std::memory_order_release);
    next_calibration_ns_.store(UINT64_MAX, std::memory_order_release);
    calibration_count_.store(0, std::memory_order_release);
    recording_started_claimed_.store(false, std::memory_order_release);
    recording_started_accepted_.store(false, std::memory_order_release);
    accepting_snapshots_.store(true, std::memory_order_release);
    state_.store(AdapterState::active, std::memory_order_release);
    host_.log(LogLevel::info, "native producer started; awaiting actual output-frame clock anchor");
    host_.log(LogLevel::info, path_to_utf8(journal_path_));
}

void ObsJournalAdapter::process_stop(const RecordingSignal& signal) noexcept {
    if (!producer_) {
        fail_current_run(ProducerResult::producer_internal_error, "stop without producer");
        return;
    }
    accepting_snapshots_.store(false, std::memory_order_release);
    const auto relative = signal.absolute_monotonic_ns >= origin_ns_.load(std::memory_order_acquire)
                              ? signal.absolute_monotonic_ns - origin_ns_.load(std::memory_order_acquire)
                              : 0;
    const std::string final_path(signal.path.view());
    const bool callbacks_done = wait_for_callbacks();
    ProducerResult stop_result = ProducerResult::producer_internal_error;
    if (callbacks_done && recording_started_accepted_.load(std::memory_order_acquire) &&
        final_path == recording_path_ && signal.absolute_monotonic_ns >= origin_ns_.load()) {
        stop_result = producer_->normal_stop(RecordingStop{
            ClockSnapshot{relative, signal.output_frame_count, false}, final_path});
    } else {
        host_.log(LogLevel::error, "final recording path/QPC does not match begun Direct MP4 run");
    }
    const auto shutdown_result = producer_->shutdown();
    const auto stable = shutdown_result != ProducerResult::producer_ok ? shutdown_result
                                                                       : producer_->result();
    const bool success = callbacks_done && stop_result == ProducerResult::producer_ok &&
                         stable == ProducerResult::producer_ok;
    const auto session = producer_->recording_session_id();
    const auto journal_utf8 = path_to_utf8(journal_path_);
    {
        std::lock_guard lock(report_mutex_);
        last_report_ = RunReport{
            stable,
            journal_utf8,
            session,
            signal.output_frame_count,
            calibration_count_.load(std::memory_order_acquire),
            success,
        };
    }
    host_.release_recording_output();
    producer_.reset();
    recording_path_.clear();
    journal_path_.clear();
    if (success) {
        state_.store(AdapterState::idle, std::memory_order_release);
        host_.log(LogLevel::info, "producer shutdown successful; Legacy Journal 1.0 stable");
        host_.log(LogLevel::info, journal_utf8);
        host_.log(LogLevel::info, session);
        host_.log(
            LogLevel::info,
            std::string("producer_result=") + to_string(stable) +
                " final_frame_count=" + std::to_string(signal.output_frame_count));
    } else {
        state_.store(AdapterState::failed, std::memory_order_release);
        host_.log(LogLevel::error, to_string(stable));
        host_.log(LogLevel::error, "recording run failed closed; journal must not be finalized");
    }
}

void ObsJournalAdapter::fail_current_run(
    const ProducerResult result,
    const std::string_view reason) noexcept {
    accepting_snapshots_.store(false, std::memory_order_release);
    RunReport report;
    report.result = result;
    report.journal_path_utf8 = path_to_utf8(journal_path_);
    report.recording_session_id = producer_ ? producer_->recording_session_id() : std::string{};
    report.calibration_count = calibration_count_.load(std::memory_order_acquire);
    report.successful = false;
    {
        std::lock_guard lock(report_mutex_);
        last_report_ = std::move(report);
    }
    state_.store(AdapterState::failed, std::memory_order_release);
    host_.release_recording_output();
    host_.log(LogLevel::error, reason);
}

void ObsJournalAdapter::worker_main() noexcept {
    try {
        for (;;) {
            std::optional<RecordingSignal> start;
            std::optional<RecordingSignal> stop;
            bool forced = false;
            bool unload = false;
            {
                std::unique_lock lock(command_mutex_);
                command_changed_.wait(lock, [&] {
                    return pending_start_.has_value() || pending_stop_.has_value() ||
                           forced_shutdown_.load(std::memory_order_acquire) || unload_requested_;
                });
                start = std::move(pending_start_);
                pending_start_.reset();
                stop = std::move(pending_stop_);
                pending_stop_.reset();
                forced = forced_shutdown_.exchange(false, std::memory_order_acq_rel);
                unload = unload_requested_;
            }
            if (start.has_value()) {
                process_start(*start);
            }
            if (stop.has_value()) {
                process_stop(*stop);
            }
            if (forced && producer_) {
                accepting_snapshots_.store(false, std::memory_order_release);
                const auto result = producer_->shutdown();
                {
                    std::lock_guard lock(report_mutex_);
                    last_report_ = RunReport{
                        result,
                        path_to_utf8(journal_path_),
                        producer_->recording_session_id(),
                        0,
                        calibration_count_.load(std::memory_order_acquire),
                        false,
                    };
                }
                static_cast<void>(wait_for_callbacks());
                host_.release_recording_output();
                producer_.reset();
                state_.store(AdapterState::failed, std::memory_order_release);
            }
            if (unload) {
                break;
            }
        }
    } catch (...) {
        accepting_snapshots_.store(false, std::memory_order_release);
        state_.store(AdapterState::failed, std::memory_order_release);
    }
    {
        std::lock_guard lock(worker_done_mutex_);
        worker_done_ = true;
    }
    worker_done_changed_.notify_all();
}

}  // namespace matrix_auto_cutter::obs_adapter
