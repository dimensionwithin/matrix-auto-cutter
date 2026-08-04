#include "matrix_auto_cutter/obs_adapter.hpp"

#include <Windows.h>

#include <algorithm>
#include <cctype>
#include <system_error>
#include <utility>

namespace matrix_auto_cutter::obs_adapter {
namespace {

constexpr std::uint64_t nominal_video_fps = 60;
constexpr std::uint64_t nanoseconds_per_second = 1'000'000'000ULL;

bool is_utf8_continuation(const unsigned char value) noexcept {
    return (value & 0xc0U) == 0x80U;
}

bool valid_utf8(const std::string_view value) noexcept {
    for (std::size_t index = 0; index < value.size();) {
        const auto first = static_cast<unsigned char>(value[index]);
        if (first <= 0x7fU) {
            ++index;
            continue;
        }
        std::size_t count = 0;
        std::uint32_t code = 0;
        if ((first & 0xe0U) == 0xc0U) {
            count = 2;
            code = first & 0x1fU;
        } else if ((first & 0xf0U) == 0xe0U) {
            count = 3;
            code = first & 0x0fU;
        } else if ((first & 0xf8U) == 0xf0U) {
            count = 4;
            code = first & 0x07U;
        } else {
            return false;
        }
        if (index + count > value.size()) {
            return false;
        }
        for (std::size_t offset = 1; offset < count; ++offset) {
            const auto next = static_cast<unsigned char>(value[index + offset]);
            if (!is_utf8_continuation(next)) {
                return false;
            }
            code = (code << 6U) | (next & 0x3fU);
        }
        if ((count == 2 && code < 0x80U) || (count == 3 && code < 0x800U) ||
            (count == 4 && code < 0x10000U) || code > 0x10ffffU ||
            (code >= 0xd800U && code <= 0xdfffU)) {
            return false;
        }
        index += count;
    }
    return true;
}

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

std::string producer_status_text(const ProducerStatus& status) {
    return std::string("state=") + to_string(status.state) +
           " result=" + to_string(status.result) +
           " read=" + std::to_string(status.read_position) +
           " write=" + std::to_string(status.write_position) +
           " durable=" + std::to_string(status.durable_position) +
           " writer_failure=" + to_string(status.writer_failure) +
           " failure_qpc=" + std::to_string(status.failure_monotonic_ns) +
           " failure_counter=" + std::to_string(status.failure_output_frame_count) +
           " pause_counter=" + std::to_string(status.failure_pause_counter);
}

std::optional<std::filesystem::path> default_local_app_data() noexcept {
    try {
        SetLastError(ERROR_SUCCESS);
        const DWORD required = GetEnvironmentVariableW(L"LOCALAPPDATA", nullptr, 0);
        if (required <= 1) {
            return std::nullopt;
        }
        std::wstring value(static_cast<std::size_t>(required), L'\0');
        const DWORD written = GetEnvironmentVariableW(L"LOCALAPPDATA", value.data(), required);
        if (written == 0 || written >= required) {
            return std::nullopt;
        }
        value.resize(static_cast<std::size_t>(written));
        std::filesystem::path path(std::move(value));
        if (path.empty() || !path.is_absolute()) {
            return std::nullopt;
        }
        return path;
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

bool SceneSignal::assign_uuid(const std::string_view value) noexcept {
    if (value.empty() || value.size() > max_scene_uuid_utf8 ||
        value.find('\0') != std::string_view::npos) {
        uuid_size = 0;
        uuid[0] = '\0';
        return false;
    }
    std::copy(value.begin(), value.end(), uuid.begin());
    uuid_size = value.size();
    uuid[uuid_size] = '\0';
    return true;
}

bool SceneSignal::assign_label(const std::string_view value) noexcept {
    if (value.empty() || value.size() > max_scene_label_utf8 ||
        value.find('\0') != std::string_view::npos || !valid_utf8(value)) {
        label_size = 0;
        label[0] = '\0';
        return false;
    }
    std::copy(value.begin(), value.end(), label.begin());
    label_size = value.size();
    label[label_size] = '\0';
    return true;
}

std::string_view SceneSignal::uuid_view() const noexcept {
    return uuid_size <= max_scene_uuid_utf8 ? std::string_view(uuid.data(), uuid_size)
                                            : std::string_view{};
}

std::string_view SceneSignal::label_view() const noexcept {
    return label_size <= max_scene_label_utf8 ? std::string_view(label.data(), label_size)
                                              : std::string_view{};
}

ObsJournalAdapter::ObsJournalAdapter(
    AdapterHost& host,
    ProducerFactory& factory,
    AdapterOptions options)
    : host_(host), factory_(factory), options_(std::move(options)) {
    if (!options_.local_app_data_provider) {
        options_.local_app_data_provider = default_local_app_data;
    }
}

ObsJournalAdapter::~ObsJournalAdapter() { unload(); }

bool ObsJournalAdapter::load() noexcept {
    if (permanently_unloaded_.load(std::memory_order_acquire)) {
        return false;
    }
    bool expected = false;
    if (!loaded_.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) {
        return true;
    }
    try {
        if (options_.callback_lifetime_factory) {
            callback_lifetime_ = options_.callback_lifetime_factory();
            if (!callback_lifetime_) {
                loaded_.store(false, std::memory_order_release);
                return false;
            }
        }
        std::unique_ptr<WorkerThreadLifetime> worker_lifetime;
        if (options_.worker_lifetime_factory) {
            worker_lifetime = options_.worker_lifetime_factory();
            if (!worker_lifetime) {
                callback_lifetime_.reset();
                loaded_.store(false, std::memory_order_release);
                return false;
            }
        }
        callback_gate_.store(0, std::memory_order_release);
        {
            std::lock_guard lock(worker_done_mutex_);
            worker_done_ = false;
        }
        worker_ = std::thread([this, lifetime = std::move(worker_lifetime)]() mutable noexcept {
            worker_main();
            if (lifetime) {
                lifetime->exit_thread();
            }
        });
        if (!host_.install_callbacks(frontend_boundary, tick_boundary, this)) {
            close_callback_gate();
            host_.remove_callbacks();
            {
                std::lock_guard lock(command_mutex_);
                unload_requested_ = true;
            }
            command_changed_.notify_one();
            worker_.join();
            loaded_.store(false, std::memory_order_release);
            state_.store(AdapterState::unloaded, std::memory_order_release);
            return false;
        }
        state_.store(AdapterState::idle, std::memory_order_release);
        host_.log(
            LogLevel::info,
            "module loaded: Matrix Auto Cutter OBS Journal Adapter 0.1.0-experimental");
        return true;
    } catch (...) {
        close_callback_gate();
        loaded_.store(false, std::memory_order_release);
        state_.store(AdapterState::unloaded, std::memory_order_release);
        callback_lifetime_.reset();
        return false;
    }
}

void ObsJournalAdapter::unload() noexcept {
    if (!loaded_.exchange(false, std::memory_order_acq_rel)) {
        return;
    }
    permanently_unloaded_.store(true, std::memory_order_release);
    try {
        close_callback_gate();
        host_.remove_callbacks();
        accepting_snapshots_.store(false, std::memory_order_release);
        {
            std::lock_guard lock(command_mutex_);
            state_.store(AdapterState::unloading, std::memory_order_release);
            unload_requested_ = true;
            forced_shutdown_.store(true, std::memory_order_release);
        }
        command_changed_.notify_one();
        bool done = false;
        {
            std::unique_lock lock(worker_done_mutex_);
            done = worker_done_changed_.wait_for(lock, options_.unload_deadline, [&] {
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
                    "adapter worker unload deadline exceeded; DLL remains pinned until final callback/thread exit");
            }
        }
        if (done) {
            state_.store(AdapterState::unloaded, std::memory_order_release);
            host_.log(LogLevel::info, "module callbacks, output signals, references and threads released");
        }
    } catch (...) {
        if (worker_.joinable()) {
            try {
                worker_.detach();
            } catch (...) {
            }
        }
    }
}

AdapterState ObsJournalAdapter::state() const noexcept {
    return state_.load(std::memory_order_acquire);
}

unsigned ObsJournalAdapter::pending_pause_resume_commands() const noexcept {
    return pause_resume_commands_pending_.load(std::memory_order_acquire);
}

std::size_t ObsJournalAdapter::pending_scene_change_commands() const noexcept {
    try {
        std::lock_guard lock(command_mutex_);
        return scene_size_;
    } catch (...) {
        return 0;
    }
}

std::optional<RunReport> ObsJournalAdapter::last_report() const {
    std::lock_guard lock(report_mutex_);
    return last_report_;
}

bool ObsJournalAdapter::enter_callback() noexcept {
    auto current = callback_gate_.load(std::memory_order_acquire);
    for (;;) {
        if ((current & callback_gate_closed) != 0 ||
            (current & callback_gate_count_mask) == callback_gate_count_mask) {
            return false;
        }
        if (callback_gate_.compare_exchange_weak(
                current, current + 1, std::memory_order_acq_rel, std::memory_order_acquire)) {
            return true;
        }
    }
}

void ObsJournalAdapter::leave_callback(const bool bound_access) noexcept {
    if (bound_access && bound_callbacks_in_flight_.fetch_sub(1, std::memory_order_acq_rel) == 1) {
        callback_finished_.notify_all();
    }
    const auto previous = callback_gate_.fetch_sub(1, std::memory_order_acq_rel);
    if ((previous & callback_gate_count_mask) == 1) {
        callback_finished_.notify_all();
    }
}

void ObsJournalAdapter::close_callback_gate() noexcept {
    callback_gate_.fetch_or(callback_gate_closed, std::memory_order_acq_rel);
    callback_finished_.notify_all();
}

void ObsJournalAdapter::probe_callback(const CallbackKind kind) {
    if (options_.callback_probe) {
        options_.callback_probe(kind);
    }
}

void ObsJournalAdapter::frontend_boundary(const FrontendEvent event, void* private_data) noexcept {
    auto* self = static_cast<ObsJournalAdapter*>(private_data);
    if (self == nullptr || !self->enter_callback()) {
        return;
    }
    const bool bound_access = event == FrontendEvent::scene_changed;
    if (bound_access) {
        self->bound_callbacks_in_flight_.fetch_add(1, std::memory_order_acq_rel);
    }
    struct Guard final {
        ObsJournalAdapter* self;
        bool bound_access;
        ~Guard() { self->leave_callback(bound_access); }
    } guard{self, bound_access};
    try {
        self->probe_callback(CallbackKind::frontend);
        self->on_frontend(event);
    } catch (...) {
        static_cast<void>(self->queue_forced_failure());
    }
}

void ObsJournalAdapter::tick_boundary(void* private_data) noexcept {
    auto* self = static_cast<ObsJournalAdapter*>(private_data);
    if (self == nullptr || !self->enter_callback()) {
        return;
    }
    self->bound_callbacks_in_flight_.fetch_add(1, std::memory_order_acq_rel);
    struct Guard final {
        ObsJournalAdapter* self;
        ~Guard() { self->leave_callback(true); }
    } guard{self};
    try {
        self->probe_callback(CallbackKind::tick);
        self->on_tick();
    } catch (...) {
        static_cast<void>(self->queue_forced_failure());
    }
}

void ObsJournalAdapter::output_boundary(
    const OutputEvent event,
    const int code,
    void* private_data) noexcept {
    auto* self = static_cast<ObsJournalAdapter*>(private_data);
    if (self == nullptr || !self->enter_callback()) {
        return;
    }
    self->bound_callbacks_in_flight_.fetch_add(1, std::memory_order_acq_rel);
    struct Guard final {
        ObsJournalAdapter* self;
        ~Guard() { self->leave_callback(true); }
    } guard{self};
    try {
        self->probe_callback(
            event == OutputEvent::started ? CallbackKind::output_start
            : event == OutputEvent::paused  ? CallbackKind::output_pause
            : event == OutputEvent::resumed ? CallbackKind::output_resume
                                            : CallbackKind::output_stop);
        self->on_output(event, code);
    } catch (...) {
        static_cast<void>(self->queue_forced_failure());
    }
}

void ObsJournalAdapter::on_frontend(const FrontendEvent event) noexcept {
    if (event == FrontendEvent::scene_changed) {
        on_scene_changed();
        return;
    }
    if (event == FrontendEvent::recording_started) {
        host_.log(LogLevel::info, "frontend RECORDING_STARTED observed (diagnostic only)");
        return;
    }
    if (event == FrontendEvent::recording_stopping) {
        host_.log(LogLevel::info, "frontend RECORDING_STOPPING observed (diagnostic only)");
        return;
    }
    if (event == FrontendEvent::recording_stopped) {
        host_.log(LogLevel::info, "frontend RECORDING_STOPPED observed (diagnostic only)");
        return;
    }
    if (event != FrontendEvent::recording_starting) {
        return;
    }

    host_.log(LogLevel::info, "frontend RECORDING_STARTING recognized");
    auto expected = AdapterState::idle;
    if (!state_.compare_exchange_strong(
            expected,
            AdapterState::start_pending,
            std::memory_order_acq_rel,
            std::memory_order_acquire)) {
        host_.log(LogLevel::warning, "concurrent recording start rejected fail closed");
        return;
    }

    if (!host_.acquire_recording_output()) {
        expected = AdapterState::start_pending;
        state_.compare_exchange_strong(expected, AdapterState::idle, std::memory_order_acq_rel);
        host_.log(LogLevel::error, "recording start rejected: no current recording output");
        return;
    }
    output_reference_held_.store(true, std::memory_order_release);
    observed_pause_state_.store(0, std::memory_order_release);
    pause_resume_commands_pending_.store(0, std::memory_order_release);
    pause_seen_.store(false, std::memory_order_release);
    if (!host_.connect_recording_output_signals(output_boundary, this)) {
        release_output_reference();
        expected = AdapterState::start_pending;
        state_.compare_exchange_strong(expected, AdapterState::idle, std::memory_order_acq_rel);
        host_.log(LogLevel::error, "recording output signal connection failed");
        return;
    }
    output_signals_connected_.store(true, std::memory_order_release);
    host_.log(
        LogLevel::info,
        "recording output bound; official output start/pause/unpause/stop signals connected");
}

void ObsJournalAdapter::on_scene_changed() noexcept {
    const auto current = state_.load(std::memory_order_acquire);
    if (current != AdapterState::active ||
        !accepting_snapshots_.load(std::memory_order_acquire)) {
        host_.log(LogLevel::warning, "program scene change ignored: recording is not active");
        return;
    }
    if (!recording_started_accepted_.load(std::memory_order_acquire)) {
        host_.log(
            LogLevel::warning,
            "program scene change ignored: recording_started is not yet accepted");
        return;
    }
    if (observed_pause_state_.load(std::memory_order_acquire) != 0U) {
        host_.log(LogLevel::warning, "program scene change ignored: recording is paused");
        return;
    }
    if (pause_resume_commands_pending_.load(std::memory_order_acquire) != 0U) {
        host_.log(
            LogLevel::warning,
            "program scene change ignored: pause/resume control transition is open");
        return;
    }

    SceneHandle scene = host_.acquire_current_program_scene();
    if (scene == nullptr) {
        host_.log(LogLevel::warning, "program scene change ignored: current scene is null");
        return;
    }
    struct SceneGuard final {
        AdapterHost& host;
        SceneHandle scene;
        ~SceneGuard() { host.release_scene(scene); }
    } scene_guard{host_, scene};

    SceneCommand command;
    const auto uuid = host_.scene_uuid(scene);
    if (!command.signal.assign_uuid(uuid) || !valid_uuid_v4(command.signal.uuid_view())) {
        host_.log(
            LogLevel::warning,
            "program scene change ignored: scene UUID is missing, invalid or unbounded");
        return;
    }
    if (!command.signal.assign_label(host_.scene_name(scene))) {
        host_.log(
            LogLevel::warning,
            "program scene change ignored: exact scene name is missing, invalid or unbounded");
        return;
    }
    if (!host_.capture_clock(
            command.signal.absolute_monotonic_ns, command.signal.output_frame_count)) {
        host_.log(LogLevel::warning, "program scene change ignored: clock capture failed");
        return;
    }

    const auto origin = origin_ns_.load(std::memory_order_acquire);
    const auto started_ns = recording_started_ns_.load(std::memory_order_acquire);
    const auto started_frames = recording_started_frame_count_.load(std::memory_order_acquire);
    if (command.signal.absolute_monotonic_ns < origin ||
        command.signal.absolute_monotonic_ns < started_ns ||
        command.signal.output_frame_count < started_frames) {
        host_.log(
            LogLevel::warning,
            "program scene change ignored: captured clock is incomplete or non-monotone");
        return;
    }

    std::unique_lock lock(command_mutex_, std::try_to_lock);
    if (!lock.owns_lock()) {
        host_.log(LogLevel::warning, "program scene change ignored: bounded command path is busy");
        return;
    }
    if (state_.load(std::memory_order_acquire) != AdapterState::active ||
        !accepting_snapshots_.load(std::memory_order_acquire) ||
        !recording_started_accepted_.load(std::memory_order_acquire) ||
        observed_pause_state_.load(std::memory_order_acquire) != 0U ||
        pause_resume_commands_pending_.load(std::memory_order_acquire) != 0U) {
        lock.unlock();
        host_.log(
            LogLevel::warning,
            "program scene change ignored: recording/control state changed during capture");
        return;
    }
    if (scene_size_ == scene_change_command_capacity) {
        lock.unlock();
        host_.log(
            LogLevel::warning,
            "program scene change ignored: bounded scene command queue is full");
        return;
    }
    if (!command_clock_is_monotone_locked(
            command.signal.absolute_monotonic_ns, command.signal.output_frame_count)) {
        lock.unlock();
        host_.log(
            LogLevel::warning,
            "program scene change ignored: clock regressed at adapter linearization");
        return;
    }
    if (!assign_command_order_locked(command.order)) {
        lock.unlock();
        host_.log(
            LogLevel::warning,
            "program scene change ignored: global adapter order is exhausted");
        return;
    }
    remember_command_clock_locked(
        command.signal.absolute_monotonic_ns, command.signal.output_frame_count);
    scene_commands_[scene_write_] = command;
    scene_write_ = (scene_write_ + 1) % scene_change_command_capacity;
    ++scene_size_;
    lock.unlock();
    command_changed_.notify_one();
    host_.log(LogLevel::info, "program scene change value snapshot queued for adapter worker");
}

bool ObsJournalAdapter::queue_forced_failure() noexcept {
    accepting_snapshots_.store(false, std::memory_order_release);
    forced_shutdown_.store(true, std::memory_order_release);
    command_changed_.notify_one();
    return false;
}

bool ObsJournalAdapter::assign_command_order_locked(std::uint64_t& order) noexcept {
    if (next_command_order_ == UINT64_MAX) {
        return false;
    }
    order = next_command_order_++;
    return true;
}

bool ObsJournalAdapter::command_clock_is_monotone_locked(
    const std::uint64_t absolute_monotonic_ns,
    const std::uint64_t output_frame_count) const noexcept {
    return !last_linearized_command_clock_.has_value() ||
           (absolute_monotonic_ns >= last_linearized_command_clock_->monotonic_ns &&
            output_frame_count >= last_linearized_command_clock_->output_frame_count);
}

void ObsJournalAdapter::remember_command_clock_locked(
    const std::uint64_t absolute_monotonic_ns,
    const std::uint64_t output_frame_count) noexcept {
    last_linearized_command_clock_ =
        ClockSnapshot{absolute_monotonic_ns, output_frame_count, false};
}

void ObsJournalAdapter::on_output(const OutputEvent event, const int code) noexcept {
    const auto current = state_.load(std::memory_order_acquire);
    if (event == OutputEvent::started) {
        if (current == AdapterState::active) {
            host_.log(LogLevel::error, "duplicate actual output start rejected fail closed");
            static_cast<void>(queue_forced_failure());
            return;
        }
        if (current != AdapterState::start_pending) {
            return;
        }
        RecordingSignal signal;
        if (!host_.capture_recording_output(signal) || !direct_mp4_signal(signal)) {
            host_.log(LogLevel::error, "actual output start snapshot failed");
            static_cast<void>(queue_forced_failure());
            return;
        }
        std::unique_lock lock(command_mutex_, std::try_to_lock);
        if (!lock.owns_lock() || control_size_ == control_command_capacity) {
            if (lock.owns_lock()) {
                lock.unlock();
            }
            host_.log(LogLevel::error, "actual output start command could not be queued");
            static_cast<void>(queue_forced_failure());
            return;
        }
        ControlCommand command{ControlKind::start, signal, 0, 0, 0, true};
        if (!assign_command_order_locked(command.order)) {
            lock.unlock();
            host_.log(LogLevel::error, "global adapter command order exhausted on output start");
            static_cast<void>(queue_forced_failure());
            return;
        }
        last_linearized_command_clock_.reset();
        remember_command_clock_locked(signal.absolute_monotonic_ns, signal.output_frame_count);
        control_commands_[control_write_] = command;
        control_write_ = (control_write_ + 1) % control_command_capacity;
        ++control_size_;
        lock.unlock();
        command_changed_.notify_one();
        host_.log(LogLevel::info, "actual output start signal recognized; producer start queued");
        return;
    }

    if (event == OutputEvent::paused || event == OutputEvent::resumed) {
        std::uint64_t absolute = 0;
        std::uint64_t frames = 0;
        if (!host_.capture_clock(absolute, frames)) {
            host_.log(LogLevel::error, "pause/resume clock capture failed; run forced fail closed");
            static_cast<void>(queue_forced_failure());
            return;
        }
        const auto captured_state = state_.load(std::memory_order_acquire);
        if (captured_state == AdapterState::stopping || captured_state == AdapterState::failed ||
            captured_state == AdapterState::unloading || captured_state == AdapterState::unloaded ||
            captured_state == AdapterState::idle) {
            return;
        }
        std::unique_lock lock(command_mutex_, std::try_to_lock);
        if (!lock.owns_lock()) {
            host_.log(LogLevel::error, "bounded control command queue was busy on pause/resume");
            static_cast<void>(queue_forced_failure());
            return;
        }
        const auto locked_state = state_.load(std::memory_order_acquire);
        if (locked_state == AdapterState::stopping || locked_state == AdapterState::failed ||
            locked_state == AdapterState::unloading || locked_state == AdapterState::unloaded ||
            locked_state == AdapterState::idle) {
            return;
        }
        if (!recording_started_accepted_.load(std::memory_order_acquire)) {
            lock.unlock();
            host_.log(LogLevel::error, "pause/resume before an active journal run rejected fail closed");
            static_cast<void>(queue_forced_failure());
            return;
        }
        const unsigned observed = observed_pause_state_.load(std::memory_order_acquire);
        if ((event == OutputEvent::paused && observed != 0U) ||
            (event == OutputEvent::resumed && observed != 1U)) {
            lock.unlock();
            host_.log(LogLevel::error, "invalid actual output pause/resume sequence rejected fail closed");
            static_cast<void>(queue_forced_failure());
            return;
        }
        if (control_size_ == control_command_capacity) {
            lock.unlock();
            host_.log(LogLevel::error, "bounded control command queue overflowed on pause/resume");
            static_cast<void>(queue_forced_failure());
            return;
        }
        const auto kind = event == OutputEvent::paused ? ControlKind::pause : ControlKind::resume;
        const bool paused = event == OutputEvent::paused;
        ControlCommand command{kind, {}, absolute, frames, 0, true, paused};
        if (!command_clock_is_monotone_locked(absolute, frames)) {
            lock.unlock();
            host_.log(
                LogLevel::error,
                "pause/resume clock regressed at adapter linearization");
            static_cast<void>(queue_forced_failure());
            return;
        }
        if (!assign_command_order_locked(command.order)) {
            lock.unlock();
            host_.log(
                LogLevel::error,
                "global adapter command order exhausted on pause/resume");
            static_cast<void>(queue_forced_failure());
            return;
        }
        remember_command_clock_locked(absolute, frames);
        control_commands_[control_write_] = command;
        control_write_ = (control_write_ + 1) % control_command_capacity;
        ++control_size_;
        pause_resume_commands_pending_.fetch_add(1, std::memory_order_release);
        observed_pause_state_.store(paused ? 1U : 0U, std::memory_order_release);
        if (paused) {
            pause_seen_.store(true, std::memory_order_release);
            state_.store(AdapterState::paused, std::memory_order_release);
        }
        lock.unlock();
        command_changed_.notify_one();
        host_.log(
            LogLevel::info,
            event == OutputEvent::paused ? "actual output pause signal queued"
                                         : "actual output unpause signal queued");
        return;
    }

    RecordingSignal signal;
    const bool captured = host_.capture_recording_output(signal);
    std::unique_lock lock(command_mutex_, std::try_to_lock);
    if (!lock.owns_lock()) {
        host_.log(LogLevel::error, "actual output stop command could not be queued");
        static_cast<void>(queue_forced_failure());
        return;
    }
    const auto locked_state = state_.load(std::memory_order_acquire);
    if (locked_state == AdapterState::idle || locked_state == AdapterState::stopping ||
        locked_state == AdapterState::failed || locked_state == AdapterState::unloading ||
        locked_state == AdapterState::unloaded) {
        return;
    }
    if (control_size_ == control_command_capacity) {
        lock.unlock();
        host_.log(LogLevel::error, "actual output stop command could not be queued");
        static_cast<void>(queue_forced_failure());
        return;
    }
    const bool paused = observed_pause_state_.load(std::memory_order_acquire) == 1U;
    ControlCommand command{ControlKind::stop, signal, 0, 0, code, captured, paused};
    if (captured && !command_clock_is_monotone_locked(
                        signal.absolute_monotonic_ns, signal.output_frame_count)) {
        lock.unlock();
        host_.log(LogLevel::error, "output stop clock regressed at adapter linearization");
        static_cast<void>(queue_forced_failure());
        return;
    }
    if (!assign_command_order_locked(command.order)) {
        lock.unlock();
        host_.log(LogLevel::error, "global adapter command order exhausted on output stop");
        static_cast<void>(queue_forced_failure());
        return;
    }
    if (captured) {
        remember_command_clock_locked(
            signal.absolute_monotonic_ns, signal.output_frame_count);
    }
    control_commands_[control_write_] = command;
    control_write_ = (control_write_ + 1) % control_command_capacity;
    ++control_size_;
    accepting_snapshots_.store(false, std::memory_order_release);
    state_.store(AdapterState::stopping, std::memory_order_release);
    lock.unlock();
    command_changed_.notify_one();
    host_.log(
        code == 0 && captured ? LogLevel::info : LogLevel::error,
        code == 0 && captured
            ? "actual output stop signal recognized with success code"
            : "actual output stop signal reported failure; run remains unfinalizable");
}

void ObsJournalAdapter::on_tick() noexcept {
    if (state_.load(std::memory_order_acquire) != AdapterState::active ||
        !accepting_snapshots_.load(std::memory_order_acquire) ||
        observed_pause_state_.load(std::memory_order_acquire) != 0 ||
        pause_resume_commands_pending_.load(std::memory_order_acquire) != 0) {
        return;
    }
    std::uint64_t absolute = 0;
    std::uint64_t frames = 0;
    if (!host_.capture_clock(absolute, frames)) {
        host_.log(LogLevel::error, "calibration capture failed; run forced fail closed");
        static_cast<void>(queue_forced_failure());
        return;
    }
    if (state_.load(std::memory_order_acquire) != AdapterState::active ||
        !accepting_snapshots_.load(std::memory_order_acquire) ||
        observed_pause_state_.load(std::memory_order_acquire) != 0U ||
        pause_resume_commands_pending_.load(std::memory_order_acquire) != 0U) {
        return;
    }
    const auto origin = origin_ns_.load(std::memory_order_acquire);
    if (absolute < origin) {
        host_.log(LogLevel::error, "non-monotone output clock; run forced fail closed");
        static_cast<void>(queue_forced_failure());
        return;
    }
    const auto relative = absolute - origin;
    ClockCommand command{absolute, frames, false};
    std::unique_lock lock(command_mutex_, std::try_to_lock);
    if (!lock.owns_lock()) {
        host_.log(LogLevel::error, "bounded adapter command queue rejected clock snapshot");
        static_cast<void>(queue_forced_failure());
        return;
    }
    if (state_.load(std::memory_order_acquire) != AdapterState::active ||
        !accepting_snapshots_.load(std::memory_order_acquire) ||
        observed_pause_state_.load(std::memory_order_acquire) != 0U ||
        pause_resume_commands_pending_.load(std::memory_order_acquire) != 0U) {
        return;
    }
    if (!recording_started_claimed_.load(std::memory_order_acquire)) {
        const auto initial_frames = initial_frame_count_.load(std::memory_order_acquire);
        if (frames <= initial_frames) {
            return;
        }
        const auto elapsed = frame_span_ns(frames - initial_frames);
        if (!elapsed.has_value() || relative < *elapsed || initial_frames == UINT64_MAX) {
            host_.log(LogLevel::error, "output frame/QPC anchor is invalid; run failed closed");
            static_cast<void>(queue_forced_failure());
            return;
        }
        bool expected = false;
        if (!recording_started_claimed_.compare_exchange_strong(
                expected, true, std::memory_order_acq_rel, std::memory_order_acquire)) {
            return;
        }
        command.recording_start_anchor = true;
    } else {
        if (!recording_started_accepted_.load(std::memory_order_acquire)) {
            return;
        }
        auto due = next_calibration_ns_.load(std::memory_order_acquire);
        if (relative < due || !next_calibration_ns_.compare_exchange_strong(
                                  due,
                                  relative +
                                      static_cast<std::uint64_t>(calibration_interval.count()) *
                                          nanoseconds_per_second,
                                  std::memory_order_acq_rel,
                                  std::memory_order_acquire)) {
            return;
        }
    }
    if (pending_clock_.has_value()) {
        lock.unlock();
        host_.log(LogLevel::error, "bounded adapter command queue rejected clock snapshot");
        static_cast<void>(queue_forced_failure());
        return;
    }
    if (!command_clock_is_monotone_locked(absolute, frames)) {
        lock.unlock();
        if (command.recording_start_anchor) {
            host_.log(
                LogLevel::error,
                "recording start clock regressed at adapter linearization");
            static_cast<void>(queue_forced_failure());
        } else {
            host_.log(
                LogLevel::warning,
                "calibration ignored: clock regressed at adapter linearization");
        }
        return;
    }
    if (!assign_command_order_locked(command.order)) {
        lock.unlock();
        host_.log(LogLevel::error, "global adapter command order exhausted on clock snapshot");
        static_cast<void>(queue_forced_failure());
        return;
    }
    remember_command_clock_locked(absolute, frames);
    pending_clock_ = command;
    lock.unlock();
    command_changed_.notify_one();
}

void ObsJournalAdapter::process_start(const RecordingSignal& signal) noexcept {
    try {
        last_worker_clock_.reset();
        const std::string recording(signal.path.view());
        const std::string session =
            options_.session_uuid_factory ? options_.session_uuid_factory() : std::string{};
        if (!valid_uuid_v4(session)) {
            fail_current_run(
                ProducerResult::producer_internal_error,
                "recording session UUIDv4 generation failed");
            return;
        }
        const auto local = options_.local_app_data_provider();
        if (!local.has_value() || local->empty() || !local->is_absolute()) {
            fail_current_run(
                ProducerResult::producer_failed_io,
                "LOCALAPPDATA resolution failed; producer not started");
            return;
        }
        const auto directory = *local / L"DimensionWithin" / L"MatrixAutoCutter" /
                               L"producer" / L"journals";
        std::error_code error;
        std::filesystem::create_directories(directory, error);
        if (error || !std::filesystem::is_directory(directory, error) || error) {
            fail_current_run(
                ProducerResult::producer_failed_io,
                "normative journal directory creation failed; producer not started");
            return;
        }
        const auto journal = directory / std::filesystem::path(
                                             session + ".recording-journal.ndjson");
        const auto journal_utf8 = path_to_utf8(journal);
        if (journal_utf8.empty()) {
            fail_current_run(
                ProducerResult::producer_failed_io,
                "normative journal path conversion failed; producer not started");
            return;
        }
        auto producer = factory_.create();
        if (!producer) {
            fail_current_run(ProducerResult::producer_internal_error, "producer allocation failed");
            return;
        }
        recording_path_ = recording;
        journal_path_ = journal;
        producer_ = std::move(producer);
        const RecordingStart start{
            journal,
            recording,
            options_.producer_version,
            std::string(host_.obs_version()),
            session,
        };
        const auto started = producer_->start_recording(start);
        if (started != ProducerResult::producer_ok) {
            fail_current_run(started, "native producer start failed");
            return;
        }
        const std::string event_id =
            options_.event_uuid_factory ? options_.event_uuid_factory() : std::string{};
        if (!valid_uuid_v4(event_id)) {
            fail_current_run(
                ProducerResult::producer_internal_error,
                "recording_started event UUIDv4 generation failed");
            return;
        }
        pending_recording_started_ = EventSnapshot{
            event_id,
            EventType::recording_started,
            ClockSnapshot{0, signal.output_frame_count, false},
            std::nullopt,
            std::nullopt,
            std::nullopt,
        };
        origin_ns_.store(signal.absolute_monotonic_ns, std::memory_order_release);
        initial_frame_count_.store(signal.output_frame_count, std::memory_order_release);
        recording_started_ns_.store(0, std::memory_order_relaxed);
        recording_started_frame_count_.store(0, std::memory_order_release);
        next_calibration_ns_.store(UINT64_MAX, std::memory_order_release);
        calibration_count_.store(0, std::memory_order_release);
        recording_started_claimed_.store(false, std::memory_order_release);
        recording_started_accepted_.store(false, std::memory_order_release);
        accepting_snapshots_.store(true, std::memory_order_release);
        if (state_.load(std::memory_order_acquire) != AdapterState::stopping) {
            state_.store(
                observed_pause_state_.load(std::memory_order_acquire) == 0
                    ? AdapterState::active
                    : AdapterState::paused,
                std::memory_order_release);
        }
        host_.log(LogLevel::info, "native producer started from actual output start signal");
        host_.log(LogLevel::info, journal_utf8);
    } catch (...) {
        fail_current_run(ProducerResult::producer_internal_error, "producer start threw internally");
    }
}

void ObsJournalAdapter::process_clock(const ClockCommand& command) noexcept {
    try {
        if (!producer_) {
            return;
        }
        if (!command.recording_start_anchor &&
            !recording_started_accepted_.load(std::memory_order_acquire)) {
            return;
        }
        const auto origin = origin_ns_.load(std::memory_order_acquire);
        if (command.absolute_monotonic_ns < origin) {
            fail_current_run(ProducerResult::producer_internal_error, "clock before output epoch");
            return;
        }
        const auto relative = command.absolute_monotonic_ns - origin;
        if (command.recording_start_anchor) {
            const auto initial = initial_frame_count_.load(std::memory_order_acquire);
            const auto elapsed = command.output_frame_count >= initial
                                     ? frame_span_ns(command.output_frame_count - initial)
                                     : std::nullopt;
            if (!pending_recording_started_.has_value() || !elapsed.has_value() ||
                relative < *elapsed || initial == UINT64_MAX) {
                fail_current_run(
                    ProducerResult::producer_internal_error,
                    "recording_started anchor preparation failed");
                return;
            }
            pending_recording_started_->clock =
                ClockSnapshot{relative - *elapsed, initial + 1, false};
            const auto snapshot = pending_recording_started_->clock;
            const auto outcome = producer_->submit(std::move(*pending_recording_started_));
            pending_recording_started_.reset();
            if (outcome != CallbackResult::accepted) {
                fail_current_run(
                    outcome == CallbackResult::full
                        ? ProducerResult::producer_failed_queue_overflow
                        : ProducerResult::producer_internal_error,
                    "recording_started event rejected; run failed closed");
                return;
            }
            recording_started_ns_.store(origin + snapshot.monotonic_ns, std::memory_order_relaxed);
            recording_started_frame_count_.store(
                snapshot.output_frame_count, std::memory_order_release);
            recording_started_accepted_.store(true, std::memory_order_release);
            last_worker_clock_ = snapshot;
            next_calibration_ns_.store(
                snapshot.monotonic_ns +
                    static_cast<std::uint64_t>(calibration_interval.count()) *
                        nanoseconds_per_second,
                std::memory_order_release);
            host_.log(
                LogLevel::info,
                "recording_started captured after actual output start; clock epoch anchored");
            return;
        }
        const ClockSnapshot snapshot{relative, command.output_frame_count, false};
        const auto outcome = producer_->submit(CalibrationSnapshot{snapshot});
        if (outcome == CallbackResult::accepted) {
            last_worker_clock_ = snapshot;
            calibration_count_.fetch_add(1, std::memory_order_relaxed);
            host_.log(LogLevel::info, "calibration snapshot accepted");
            return;
        }
        fail_current_run(
            outcome == CallbackResult::full ? ProducerResult::producer_failed_queue_overflow
                                            : ProducerResult::producer_internal_error,
            std::string("producer rejected calibration; run cannot be successful; ") +
                producer_status_text(producer_->status()));
    } catch (...) {
        fail_current_run(ProducerResult::producer_internal_error, "clock processing threw");
    }
}

void ObsJournalAdapter::process_scene_changed(const SceneCommand& command) noexcept {
    try {
        // Callback admission and command.order are the linearization point. Later
        // pause/stop callbacks may already have updated their observable atomics,
        // but cannot revoke or overtake this earlier authorized value snapshot.
        if (!producer_ ||
            !recording_started_accepted_.load(std::memory_order_acquire)) {
            host_.log(
                LogLevel::warning,
                "queued program scene change discarded: its admitted run is unavailable");
            return;
        }
        const auto origin = origin_ns_.load(std::memory_order_acquire);
        const auto& signal = command.signal;
        if (signal.absolute_monotonic_ns < origin || signal.uuid_view().empty() ||
            !valid_uuid_v4(signal.uuid_view()) || signal.label_view().empty() ||
            !valid_utf8(signal.label_view())) {
            host_.log(
                LogLevel::warning,
                "queued program scene change discarded: value snapshot is invalid");
            return;
        }
        const ClockSnapshot clock{
            signal.absolute_monotonic_ns - origin, signal.output_frame_count, false};
        if (last_worker_clock_.has_value() &&
            (clock.monotonic_ns < last_worker_clock_->monotonic_ns ||
             clock.output_frame_count < last_worker_clock_->output_frame_count)) {
            host_.log(
                LogLevel::warning,
                "queued program scene change discarded: worker clock order is ambiguous");
            return;
        }
        const std::string event_id =
            options_.event_uuid_factory ? options_.event_uuid_factory() : std::string{};
        if (!valid_uuid_v4(event_id)) {
            host_.log(
                LogLevel::error,
                "queued program scene change discarded: worker UUIDv4 generation failed");
            return;
        }
        EventSnapshot snapshot{
            event_id,
            EventType::scene_changed,
            clock,
            std::string(signal.uuid_view()),
            std::nullopt,
            std::string(signal.label_view()),
        };
        const auto outcome = producer_->submit(std::move(snapshot));
        if (outcome != CallbackResult::accepted) {
            fail_current_run(
                outcome == CallbackResult::full ? ProducerResult::producer_failed_queue_overflow
                                                : ProducerResult::producer_internal_error,
                "producer rejected scene_changed snapshot; run cannot be successful");
            return;
        }
        last_worker_clock_ = clock;
        host_.log(LogLevel::info, "canonical scene_changed snapshot accepted by producer queue");
    } catch (...) {
        fail_current_run(
            ProducerResult::producer_internal_error,
            "scene_changed worker processing threw");
    }
}

void ObsJournalAdapter::process_pause_or_resume(const ControlCommand& command) noexcept {
    try {
        if (!producer_ || !recording_started_accepted_.load(std::memory_order_acquire)) {
            fail_current_run(
                ProducerResult::producer_internal_error,
                "pause/resume arrived before recording_started was journaled");
            return;
        }
        const auto origin = origin_ns_.load(std::memory_order_acquire);
        if (!command.captured || command.absolute_monotonic_ns < origin) {
            fail_current_run(
                ProducerResult::producer_internal_error,
                "pause/resume clock was not monotone from output start");
            return;
        }
        const std::string event_id =
            options_.event_uuid_factory ? options_.event_uuid_factory() : std::string{};
        if (!valid_uuid_v4(event_id)) {
            fail_current_run(
                ProducerResult::producer_internal_error,
                "pause/resume UUIDv4 generation failed");
            return;
        }
        const ClockSnapshot clock{
            command.absolute_monotonic_ns - origin, command.output_frame_count,
            command.kind == ControlKind::pause};
        host_.log(
            LogLevel::info,
            std::string(command.kind == ControlKind::pause ? "pause" : "resume") +
                " writer boundary before submit qpc=" + std::to_string(clock.monotonic_ns) +
                " counter=" + std::to_string(clock.output_frame_count) +
                " observed_pause=" +
                std::to_string(observed_pause_state_.load(std::memory_order_acquire)) +
                " pending=" +
                std::to_string(pause_resume_commands_pending_.load(std::memory_order_acquire)) +
                " " + producer_status_text(producer_->status()));
        const CallbackResult outcome = command.kind == ControlKind::pause
                                           ? producer_->submit(PauseSnapshot{event_id, clock})
                                           : producer_->submit(ResumeSnapshot{event_id, clock});
        if (outcome != CallbackResult::accepted) {
            fail_current_run(
                outcome == CallbackResult::full ? ProducerResult::producer_failed_queue_overflow
                                                : ProducerResult::producer_internal_error,
                "producer rejected pause/resume snapshot; run cannot be successful");
            return;
        }
        last_worker_clock_ = clock;
        host_.log(
            LogLevel::info,
            std::string(command.kind == ControlKind::pause ? "pause" : "resume") +
                " snapshot accepted by producer queue; " +
                producer_status_text(producer_->status()));
        const auto durable = producer_->confirm_durable();
        if (durable != ProducerResult::producer_ok) {
            fail_current_run(
                durable,
                std::string(command.kind == ControlKind::pause ? "pause" : "resume") +
                    " was not durably written by producer; run cannot be successful");
            return;
        }
        host_.log(
            LogLevel::info,
            std::string(command.kind == ControlKind::pause ? "pause" : "resume") +
                " durably written by producer writer; " +
                producer_status_text(producer_->status()));
        const auto previous_pending =
            pause_resume_commands_pending_.fetch_sub(1, std::memory_order_acq_rel);
        if (previous_pending == 0) {
            fail_current_run(
                ProducerResult::producer_internal_error,
                "pause/resume pending-command accounting underflowed");
            return;
        }
        if (command.kind == ControlKind::resume) {
            if (previous_pending == 1 &&
                state_.load(std::memory_order_acquire) != AdapterState::stopping) {
                state_.store(
                    observed_pause_state_.load(std::memory_order_acquire) == 0U
                        ? AdapterState::active
                        : AdapterState::paused,
                    std::memory_order_release);
            }
            host_.log(
                LogLevel::info,
                std::string("producer remains active after durable resume; ") +
                    producer_status_text(producer_->status()));
        } else {
            host_.log(LogLevel::info, "durable pause processing complete");
        }
    } catch (...) {
        fail_current_run(ProducerResult::producer_internal_error, "pause/resume processing threw");
    }
}

void ObsJournalAdapter::disconnect_output_signals() noexcept {
    if (output_signals_connected_.exchange(false, std::memory_order_acq_rel)) {
        host_.disconnect_recording_output_signals();
    }
}

void ObsJournalAdapter::release_output_reference() noexcept {
    if (output_reference_held_.exchange(false, std::memory_order_acq_rel)) {
        host_.release_recording_output();
    }
}

void ObsJournalAdapter::wait_for_callbacks_to_drain() noexcept {
    try {
        std::unique_lock lock(callback_wait_mutex_);
        callback_finished_.wait(lock, [&] {
            return (callback_gate_.load(std::memory_order_acquire) & callback_gate_count_mask) ==
                   0;
        });
    } catch (...) {
    }
}

void ObsJournalAdapter::wait_for_bound_callbacks_to_drain() noexcept {
    try {
        std::unique_lock lock(callback_wait_mutex_);
        callback_finished_.wait(lock, [&] {
            return bound_callbacks_in_flight_.load(std::memory_order_acquire) == 0;
        });
    } catch (...) {
    }
}

void ObsJournalAdapter::reset_run() noexcept {
    {
        std::lock_guard lock(command_mutex_);
        scene_read_ = 0;
        scene_write_ = 0;
        scene_size_ = 0;
        last_linearized_command_clock_.reset();
    }
    producer_.reset();
    pending_recording_started_.reset();
    last_worker_clock_.reset();
    recording_path_.clear();
    journal_path_.clear();
    accepting_snapshots_.store(false, std::memory_order_release);
    recording_started_claimed_.store(false, std::memory_order_release);
    recording_started_accepted_.store(false, std::memory_order_release);
    observed_pause_state_.store(0, std::memory_order_release);
    pause_resume_commands_pending_.store(0, std::memory_order_release);
    pause_seen_.store(false, std::memory_order_release);
}

void ObsJournalAdapter::process_stop(const ControlCommand& command) noexcept {
    try {
        accepting_snapshots_.store(false, std::memory_order_release);
        state_.store(AdapterState::stopping, std::memory_order_release);
        disconnect_output_signals();
        wait_for_bound_callbacks_to_drain();

        ProducerResult stop_result = ProducerResult::producer_internal_error;
        std::optional<RecordingStop> stop_request;
        if (producer_ && command.captured && command.code == 0 &&
            recording_started_accepted_.load(std::memory_order_acquire)) {
            RecordingSignal signal = command.signal;
            const auto origin = origin_ns_.load(std::memory_order_acquire);
            const bool paused = command.recording_paused;
            bool valid = signal.absolute_monotonic_ns >= origin &&
                         signal.path.view() == recording_path_;
            if (valid && !pause_seen_.load(std::memory_order_acquire)) {
                const auto started_frames =
                    recording_started_frame_count_.load(std::memory_order_acquire);
                const auto started_ns = recording_started_ns_.load(std::memory_order_acquire);
                constexpr std::uint64_t max_stop_qpc_adjustment_frames = 8;
                const auto max_adjustment = frame_span_ns(max_stop_qpc_adjustment_frames);
                const auto counter_span = signal.output_frame_count >= started_frames
                                              ? frame_span_ns(signal.output_frame_count - started_frames)
                                              : std::nullopt;
                valid = max_adjustment.has_value() && counter_span.has_value() &&
                        started_ns <= UINT64_MAX - counter_span.value_or(0);
                if (valid) {
                    const auto final_frame_ns = started_ns + *counter_span;
                    const auto difference = final_frame_ns <= signal.absolute_monotonic_ns
                                                ? signal.absolute_monotonic_ns - final_frame_ns
                                                : final_frame_ns - signal.absolute_monotonic_ns;
                    valid = difference <= *max_adjustment && final_frame_ns >= origin;
                    if (valid) {
                        signal.absolute_monotonic_ns = final_frame_ns;
                    }
                }
            }
            if (valid) {
                stop_request.emplace(RecordingStop{
                    ClockSnapshot{
                        signal.absolute_monotonic_ns - origin,
                        signal.output_frame_count,
                        paused},
                    std::string(signal.path.view()),
                });
            }
        }
        bool interrupted = false;
        {
            std::lock_guard lock(command_mutex_);
            interrupted = unload_requested_ ||
                          forced_shutdown_.exchange(false, std::memory_order_acq_rel);
            if (!interrupted && stop_request.has_value()) {
                stop_result = producer_->normal_stop(*stop_request);
            }
        }
        if (interrupted) {
            force_cleanup(
                ProducerResult::producer_internal_error,
                "stop authorization lost to unload or callback failure");
            return;
        }
        const auto shutdown_result = producer_ ? producer_->shutdown()
                                               : ProducerResult::producer_internal_error;
        const auto stable =
            shutdown_result != ProducerResult::producer_ok
                ? shutdown_result
                : producer_ ? producer_->result() : ProducerResult::producer_internal_error;
        const auto report_result = stop_result != ProducerResult::producer_ok ? stop_result : stable;
        const bool success = stop_result == ProducerResult::producer_ok &&
                             stable == ProducerResult::producer_ok && command.code == 0;
        const auto session = producer_ ? producer_->recording_session_id() : std::string{};
        const auto journal_utf8 = path_to_utf8(journal_path_);
        {
            std::lock_guard lock(report_mutex_);
            last_report_ = RunReport{
                report_result,
                journal_utf8,
                session,
                command.signal.output_frame_count,
                calibration_count_.load(std::memory_order_acquire),
                success,
            };
        }
        release_output_reference();
        reset_run();
        if (success) {
            state_.store(AdapterState::idle, std::memory_order_release);
            host_.log(LogLevel::info, "producer shutdown successful; Legacy Journal 1.0 stable");
            host_.log(LogLevel::info, journal_utf8);
            host_.log(LogLevel::info, session);
            host_.log(
                LogLevel::info,
                std::string("producer_result=") + to_string(stable) +
                    " final_frame_count=" +
                    std::to_string(command.signal.output_frame_count));
        } else {
            state_.store(AdapterState::failed, std::memory_order_release);
            host_.log(LogLevel::error, to_string(report_result));
            host_.log(
                LogLevel::error,
                "output stop was absent/failed/invalid; no finalizable stop record authorized");
        }
    } catch (...) {
        force_cleanup(ProducerResult::producer_internal_error, "stop processing threw");
    }
}

void ObsJournalAdapter::force_cleanup(
    ProducerResult result,
    const std::string_view reason) noexcept {
    if (producer_) {
        host_.log(
            LogLevel::error,
            std::string("fail-closed cleanup entered: ") + std::string(reason) + "; " +
                producer_status_text(producer_->status()));
    } else {
        host_.log(
            LogLevel::error,
            std::string("fail-closed cleanup entered without producer: ") +
                std::string(reason));
    }
    accepting_snapshots_.store(false, std::memory_order_release);
    if (state_.load(std::memory_order_acquire) != AdapterState::unloading) {
        state_.store(AdapterState::stopping, std::memory_order_release);
    }
    disconnect_output_signals();
    wait_for_bound_callbacks_to_drain();
    if (producer_) {
        const auto shutdown = producer_->shutdown();
        if (shutdown != ProducerResult::producer_ok) {
            result = shutdown;
        } else if (producer_->result() != ProducerResult::producer_ok) {
            result = producer_->result();
        }
    }
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
    release_output_reference();
    reset_run();
    if (state_.load(std::memory_order_acquire) != AdapterState::unloading) {
        state_.store(AdapterState::failed, std::memory_order_release);
    }
    host_.log(LogLevel::error, reason);
}

void ObsJournalAdapter::fail_current_run(
    const ProducerResult result,
    const std::string_view reason) noexcept {
    force_cleanup(result, reason);
}

void ObsJournalAdapter::worker_main() noexcept {
    try {
        for (;;) {
            std::optional<ControlCommand> control;
            std::optional<ClockCommand> clock;
            std::optional<SceneCommand> scene;
            enum class SelectedCommand { none, control, clock, scene };
            SelectedCommand selected{SelectedCommand::none};
            bool forced = false;
            bool unload = false;
            {
                std::unique_lock lock(command_mutex_);
                command_changed_.wait(lock, [&] {
                    return control_size_ != 0 || pending_clock_.has_value() || scene_size_ != 0 ||
                           forced_shutdown_.load(std::memory_order_acquire) || unload_requested_;
                });
                forced = forced_shutdown_.exchange(false, std::memory_order_acq_rel);
                unload = unload_requested_;
                if (forced || unload) {
                    control_read_ = 0;
                    control_write_ = 0;
                    control_size_ = 0;
                    pending_clock_.reset();
                    scene_read_ = 0;
                    scene_write_ = 0;
                    scene_size_ = 0;
                } else {
                    // Every accepted recording command receives a unique order while holding
                    // command_mutex_. Each bounded container preserves that order internally,
                    // so the smallest front order is the single global worker order. QPC/frame
                    // equality is therefore resolved by callback linearization, never by type.
                    std::uint64_t earliest = UINT64_MAX;
                    if (control_size_ != 0 &&
                        control_commands_[control_read_].order < earliest) {
                        earliest = control_commands_[control_read_].order;
                        selected = SelectedCommand::control;
                    }
                    if (pending_clock_.has_value() && pending_clock_->order < earliest) {
                        earliest = pending_clock_->order;
                        selected = SelectedCommand::clock;
                    }
                    if (scene_size_ != 0 && scene_commands_[scene_read_].order < earliest) {
                        selected = SelectedCommand::scene;
                    }
                    if (selected == SelectedCommand::control) {
                        control.emplace(std::move(control_commands_[control_read_]));
                        control_read_ = (control_read_ + 1) % control_command_capacity;
                        --control_size_;
                    } else if (selected == SelectedCommand::clock) {
                        clock = std::move(pending_clock_);
                        pending_clock_.reset();
                    } else if (selected == SelectedCommand::scene) {
                        scene.emplace(std::move(scene_commands_[scene_read_]));
                        scene_read_ = (scene_read_ + 1) % scene_change_command_capacity;
                        --scene_size_;
                    }
                }
            }
            if (unload) {
                wait_for_callbacks_to_drain();
                if (producer_ || output_reference_held_.load(std::memory_order_acquire)) {
                    force_cleanup(
                        ProducerResult::producer_internal_error,
                        "module unload before successful output stop; run failed closed");
                } else {
                    disconnect_output_signals();
                    wait_for_bound_callbacks_to_drain();
                    release_output_reference();
                }
                state_.store(AdapterState::unloading, std::memory_order_release);
                break;
            }
            if (forced) {
                force_cleanup(
                    ProducerResult::producer_internal_error,
                    "adapter command/callback failure forced fail-closed cleanup");
                continue;
            }
            if (control.has_value()) {
                if (control->kind == ControlKind::start) {
                    process_start(control->signal);
                } else if (control->kind == ControlKind::pause ||
                           control->kind == ControlKind::resume) {
                    process_pause_or_resume(*control);
                } else {
                    process_stop(*control);
                }
            } else if (clock.has_value()) {
                process_clock(*clock);
            } else if (scene.has_value()) {
                process_scene_changed(*scene);
            }
        }
    } catch (...) {
        close_callback_gate();
        host_.remove_callbacks();
        wait_for_callbacks_to_drain();
        force_cleanup(ProducerResult::producer_internal_error, "adapter worker failed internally");
    }
    callback_lifetime_.reset();
    {
        std::lock_guard lock(worker_done_mutex_);
        worker_done_ = true;
    }
    worker_done_changed_.notify_all();
}

}  // namespace matrix_auto_cutter::obs_adapter
