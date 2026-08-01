#include "matrix_auto_cutter/obs_adapter.hpp"

#include <Windows.h>
#include <obs-frontend-api.h>
#include <obs-module.h>

#include <array>
#include <algorithm>
#include <atomic>
#include <bit>
#include <climits>
#include <memory>
#include <new>
#include <cstring>
#include <stdexcept>
#include <string_view>
#include <utility>

OBS_DECLARE_MODULE()

namespace {

using matrix_auto_cutter::CallbackResult;
using matrix_auto_cutter::JournalProducer;
using matrix_auto_cutter::JournalSnapshot;
using matrix_auto_cutter::ProducerOptions;
using matrix_auto_cutter::ProducerResult;
using matrix_auto_cutter::RecordingStart;
using matrix_auto_cutter::RecordingStop;
using namespace matrix_auto_cutter::obs_adapter;

constexpr std::string_view plugin_name =
    "Matrix Auto Cutter OBS Journal Adapter 0.1.0-experimental";
int module_anchor = 0;

HMODULE retain_current_module() noexcept {
    HMODULE module{};
    static_cast<void>(GetModuleHandleExW(
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
        reinterpret_cast<LPCWSTR>(&module_anchor),
        &module));
    return module;
}

template <typename Function>
Function resolve(HMODULE module, const char* name) noexcept {
    if (module == nullptr) {
        return nullptr;
    }
    const FARPROC address = GetProcAddress(module, name);
    if (address == nullptr) {
        return nullptr;
    }
    static_assert(sizeof(Function) == sizeof(address));
    return std::bit_cast<Function>(address);
}

class Obs32Api final {
  public:
    decltype(&obs_frontend_add_event_callback) frontend_add{};
    decltype(&obs_frontend_remove_event_callback) frontend_remove{};
    decltype(&obs_frontend_get_recording_output) frontend_recording_output{};
    decltype(&obs_add_tick_callback) add_tick{};
    decltype(&obs_remove_tick_callback) remove_tick{};
    decltype(&obs_output_get_settings) output_settings{};
    decltype(&obs_data_get_string) data_string{};
    decltype(&obs_data_release) data_release{};
    decltype(&obs_output_release) output_release{};
    decltype(&obs_output_get_id) output_id{};
    decltype(&obs_output_get_total_frames) output_frames{};
    decltype(&obs_get_video_frame_time) video_frame_time{};
    decltype(&obs_get_version_string) version_string{};
    decltype(&blog) write_log{};

    bool load() noexcept {
        const HMODULE obs = GetModuleHandleW(L"obs.dll");
        const HMODULE frontend = GetModuleHandleW(L"obs-frontend-api.dll");
        frontend_add = resolve<decltype(frontend_add)>(frontend, "obs_frontend_add_event_callback");
        frontend_remove =
            resolve<decltype(frontend_remove)>(frontend, "obs_frontend_remove_event_callback");
        frontend_recording_output = resolve<decltype(frontend_recording_output)>(
            frontend, "obs_frontend_get_recording_output");
        add_tick = resolve<decltype(add_tick)>(obs, "obs_add_tick_callback");
        remove_tick = resolve<decltype(remove_tick)>(obs, "obs_remove_tick_callback");
        output_settings = resolve<decltype(output_settings)>(obs, "obs_output_get_settings");
        data_string = resolve<decltype(data_string)>(obs, "obs_data_get_string");
        data_release = resolve<decltype(data_release)>(obs, "obs_data_release");
        output_release = resolve<decltype(output_release)>(obs, "obs_output_release");
        output_id = resolve<decltype(output_id)>(obs, "obs_output_get_id");
        output_frames = resolve<decltype(output_frames)>(obs, "obs_output_get_total_frames");
        video_frame_time =
            resolve<decltype(video_frame_time)>(obs, "obs_get_video_frame_time");
        version_string = resolve<decltype(version_string)>(obs, "obs_get_version_string");
        write_log = resolve<decltype(write_log)>(obs, "blog");
        return frontend_add && frontend_remove && frontend_recording_output && add_tick &&
               remove_tick && output_settings && data_string && data_release && output_release &&
               output_id && output_frames && video_frame_time && version_string && write_log;
    }
};

class WindowsModuleThreadLifetime final : public WorkerThreadLifetime {
  public:
    WindowsModuleThreadLifetime() noexcept {
        module_ = retain_current_module();
    }

    ~WindowsModuleThreadLifetime() override {
        if (module_ != nullptr) {
            FreeLibrary(module_);
        }
    }

    [[nodiscard]] bool valid() const noexcept { return module_ != nullptr; }

    void exit_thread() noexcept override {
        const HMODULE module = std::exchange(module_, nullptr);
        if (module != nullptr) {
            FreeLibraryAndExitThread(module, 0);
        }
    }

  private:
    HMODULE module_{};
};

std::unique_ptr<WorkerThreadLifetime> make_worker_lifetime() {
    auto lifetime = std::make_unique<WindowsModuleThreadLifetime>();
    return lifetime->valid() ? std::move(lifetime) : nullptr;
}

matrix_auto_cutter::WriterThreadExit make_writer_exit() {
    const HMODULE module = retain_current_module();
    if (module == nullptr) {
        return {};
    }
    // The core moves this trivial capture out of SharedState, releases that
    // state, and invokes this as the writer thread's final operation.
    return [module]() noexcept { FreeLibraryAndExitThread(module, 0); };
}

class NativeProducerPort final : public ProducerPort {
  public:
    NativeProducerPort() : producer_([] {
        ProducerOptions options;
        options.writer_thread_exit = make_writer_exit();
        if (!options.writer_thread_exit) {
            throw std::runtime_error("cannot retain OBS plugin module for writer thread");
        }
        return options;
    }()) {}

    ProducerResult start_recording(const RecordingStart& start) noexcept override {
        return producer_.start_recording(start);
    }
    CallbackResult submit(JournalSnapshot snapshot) noexcept override {
        return producer_.submit(std::move(snapshot));
    }
    ProducerResult normal_stop(const RecordingStop& stop) noexcept override {
        return producer_.normal_stop(stop);
    }
    ProducerResult shutdown() noexcept override { return producer_.shutdown(); }
    ProducerResult result() const noexcept override { return producer_.result(); }
    std::string recording_session_id() const noexcept override {
        return producer_.recording_session_id();
    }

  private:
    JournalProducer producer_;
};

class NativeProducerFactory final : public ProducerFactory {
  public:
    std::unique_ptr<ProducerPort> create() noexcept override {
        try {
            return std::make_unique<NativeProducerPort>();
        } catch (...) {
            return nullptr;
        }
    }
};

class NativeObsHost final : public AdapterHost {
  public:
    explicit NativeObsHost(Obs32Api& api) noexcept : api_(api) {
        const char* version = api_.version_string();
        if (version != nullptr) {
            const std::string_view text(version);
            const auto count = (std::min)(text.size(), version_.size() - 1);
            std::copy_n(text.data(), count, version_.data());
            version_[count] = '\0';
            version_size_ = count;
        }
    }

    bool install_callbacks(
        const FrontendCallback frontend,
        const TickCallback tick,
        void* private_data) noexcept override {
        frontend_ = frontend;
        tick_ = tick;
        private_data_ = private_data;
        api_.frontend_add(obs_frontend_boundary, this);
        api_.add_tick(obs_tick_boundary, this);
        installed_.store(true, std::memory_order_release);
        return true;
    }

    void remove_callbacks() noexcept override {
        if (installed_.exchange(false, std::memory_order_acq_rel)) {
            api_.remove_tick(obs_tick_boundary, this);
            api_.frontend_remove(obs_frontend_boundary, this);
        }
        frontend_ = nullptr;
        tick_ = nullptr;
        private_data_ = nullptr;
    }

    bool acquire_recording_start(RecordingSignal& signal) noexcept override {
        obs_output_t* output = api_.frontend_recording_output();
        if (output == nullptr) {
            return false;
        }
        obs_output_t* expected = nullptr;
        if (!output_.compare_exchange_strong(
                expected, output, std::memory_order_acq_rel, std::memory_order_acquire)) {
            api_.output_release(output);
            return false;
        }
        if (!capture_path_and_kind(output, signal) || !capture_clock_for(output, signal)) {
            release_recording_output();
            return false;
        }
        return true;
    }

    bool capture_recording_stop(RecordingSignal& signal) noexcept override {
        obs_output_t* output = output_.load(std::memory_order_acquire);
        return output != nullptr && capture_path_and_kind(output, signal) &&
               capture_clock_for(output, signal);
    }

    bool capture_clock(
        std::uint64_t& absolute_monotonic_ns,
        std::uint64_t& output_frame_count) noexcept override {
        obs_output_t* output = output_.load(std::memory_order_acquire);
        if (output == nullptr) {
            return false;
        }
        RecordingSignal signal;
        if (!capture_clock_for(output, signal)) {
            return false;
        }
        absolute_monotonic_ns = signal.absolute_monotonic_ns;
        output_frame_count = signal.output_frame_count;
        return true;
    }

    void release_recording_output() noexcept override {
        if (obs_output_t* output = output_.exchange(nullptr, std::memory_order_acq_rel)) {
            api_.output_release(output);
        }
    }

    std::string_view obs_version() const noexcept override {
        return std::string_view(version_.data(), version_size_);
    }

    void log(const LogLevel level, const std::string_view message) noexcept override {
        const int obs_level = level == LogLevel::info ? LOG_INFO
                              : level == LogLevel::warning ? LOG_WARNING
                                                         : LOG_ERROR;
        const auto length = static_cast<int>((std::min)(message.size(), static_cast<std::size_t>(INT_MAX)));
        api_.write_log(obs_level, "[matrix-auto-cutter-obs] %.*s", length, message.data());
    }

    ~NativeObsHost() override {
        remove_callbacks();
        release_recording_output();
    }

  private:
    static void obs_frontend_boundary(const enum obs_frontend_event event, void* data) noexcept {
        try {
            auto* self = static_cast<NativeObsHost*>(data);
            if (self == nullptr || self->frontend_ == nullptr) {
                return;
            }
            const FrontendEvent mapped =
                event == OBS_FRONTEND_EVENT_RECORDING_STARTED
                    ? FrontendEvent::recording_started
                    : event == OBS_FRONTEND_EVENT_RECORDING_STOPPED
                          ? FrontendEvent::recording_stopped
                          : FrontendEvent::other;
            self->frontend_(mapped, self->private_data_);
        } catch (...) {
        }
    }

    static void obs_tick_boundary(void* data, float) noexcept {
        try {
            auto* self = static_cast<NativeObsHost*>(data);
            if (self != nullptr && self->tick_ != nullptr) {
                self->tick_(self->private_data_);
            }
        } catch (...) {
        }
    }

    bool capture_path_and_kind(obs_output_t* output, RecordingSignal& signal) noexcept {
        const char* output_id = api_.output_id(output);
        if (output_id == nullptr) {
            return false;
        }
        const std::size_t output_id_size = strnlen_s(output_id, max_recording_path_utf8 + 1);
        if (output_id_size == 0 || output_id_size > max_recording_path_utf8 ||
            !signal.output_id.assign(std::string_view(output_id, output_id_size))) {
            return false;
        }
        obs_data_t* settings = api_.output_settings(output);
        if (settings == nullptr) {
            return false;
        }
        const char* path = api_.data_string(settings, "path");
        bool valid = false;
        if (path != nullptr) {
            const std::size_t count = strnlen_s(path, max_recording_path_utf8 + 1);
            valid = count > 0 && count <= max_recording_path_utf8 &&
                    signal.path.assign(std::string_view(path, count));
        }
        const char* muxer_settings = api_.data_string(settings, "muxer_settings");
        if (muxer_settings != nullptr) {
            const std::size_t count = strnlen_s(muxer_settings, max_recording_path_utf8 + 1);
            if (count > max_recording_path_utf8) {
                valid = false;
            } else {
                const std::string_view muxer(muxer_settings, count);
                signal.fragmented_mp4 = muxer.find("frag_keyframe") != std::string_view::npos ||
                                        muxer.find("empty_moov") != std::string_view::npos ||
                                        muxer.find("delay_moov") != std::string_view::npos;
            }
        }
        api_.data_release(settings);
        return valid;
    }

    bool capture_clock_for(obs_output_t* output, RecordingSignal& signal) noexcept {
        const int frames = api_.output_frames(output);
        const std::uint64_t frame_time = api_.video_frame_time();
        if (frames < 0 || frame_time == 0) {
            return false;
        }
        // OBS 32.1.2 implements obs_get_video_frame_time() with its QPC-backed
        // os_gettime_ns() clock. This value is phase-locked to the video frame
        // whose output counter is copied at this callback boundary.
        signal.absolute_monotonic_ns = frame_time;
        signal.output_frame_count = static_cast<std::uint64_t>(frames);
        return true;
    }

    Obs32Api& api_;
    std::atomic<obs_output_t*> output_{};
    std::atomic<bool> installed_{};
    FrontendCallback frontend_{};
    TickCallback tick_{};
    void* private_data_{};
    std::array<char, 64> version_{};
    std::size_t version_size_{};
};

Obs32Api api;
std::unique_ptr<NativeObsHost> host;
std::unique_ptr<NativeProducerFactory> producer_factory;
std::unique_ptr<ObsJournalAdapter> adapter;

}  // namespace

extern "C" MODULE_EXPORT const char* obs_module_name(void) { return plugin_name.data(); }

extern "C" MODULE_EXPORT const char* obs_module_description(void) {
    return "Experimental Direct-MP4 adapter for the native Matrix Auto Cutter JournalProducer";
}

extern "C" MODULE_EXPORT bool obs_module_load(void) {
    try {
        if (!api.load()) {
            return false;
        }
        host = std::make_unique<NativeObsHost>(api);
        producer_factory = std::make_unique<NativeProducerFactory>();
        AdapterOptions options;
        options.worker_lifetime_factory = make_worker_lifetime;
        adapter = std::make_unique<ObsJournalAdapter>(*host, *producer_factory, std::move(options));
        if (!adapter->load()) {
            adapter.reset();
            producer_factory.reset();
            host.reset();
            return false;
        }
        host->log(LogLevel::info, plugin_name);
        host->log(LogLevel::info, host->obs_version());
        return true;
    } catch (...) {
        return false;
    }
}

extern "C" MODULE_EXPORT void obs_module_unload(void) {
    try {
        if (adapter) {
            adapter->unload();
        }
    } catch (...) {
    }
}
