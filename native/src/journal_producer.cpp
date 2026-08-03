#include "matrix_auto_cutter/journal_producer.hpp"

#include <Windows.h>
#include <bcrypt.h>

#include <array>
#include <atomic>
#include <cstdio>
#include <limits>
#include <optional>
#include <string>
#include <thread>
#include <unordered_set>
#include <utility>
#include <vector>

namespace matrix_auto_cutter {
namespace {

class SystemShutdownClock final : public ShutdownClock {
  public:
    std::chrono::steady_clock::time_point now() noexcept override {
        return std::chrono::steady_clock::now();
    }
};

class SystemShutdownWait final : public ShutdownWait {
  public:
    bool wait_until(
        std::condition_variable& condition,
        std::unique_lock<std::mutex>& lock,
        const std::chrono::steady_clock::time_point deadline,
        const std::function<bool()>& ready) noexcept override {
        try {
            return condition.wait_until(lock, deadline, ready);
        } catch (...) {
            return false;
        }
    }
};

class Win32JournalSink final : public JournalSink {
  public:
    explicit Win32JournalSink(const std::filesystem::path& path) noexcept
        : handle_(CreateFileW(
              path.c_str(),
              GENERIC_WRITE,
              FILE_SHARE_READ,
              nullptr,
              CREATE_NEW,
              FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
              nullptr)) {}

    ~Win32JournalSink() override {
        if (handle_ != INVALID_HANDLE_VALUE) {
            CloseHandle(handle_);
        }
    }

    [[nodiscard]] bool valid() const noexcept { return handle_ != INVALID_HANDLE_VALUE; }

    bool write_line(const std::string_view line) noexcept override {
        if (!valid() || line.size() >= std::numeric_limits<DWORD>::max()) {
            return false;
        }
        std::string complete;
        try {
            complete.reserve(line.size() + 1);
            complete.assign(line);
            complete.push_back('\n');
        } catch (...) {
            return false;
        }
        DWORD written = 0;
        const auto expected = static_cast<DWORD>(complete.size());
        if (!WriteFile(handle_, complete.data(), expected, &written, nullptr) || written != expected) {
            return false;
        }
        return FlushFileBuffers(handle_) != 0;
    }

  private:
    HANDLE handle_{INVALID_HANDLE_VALUE};
};

std::unique_ptr<JournalSink> default_sink_factory(const std::filesystem::path& path) {
    auto sink = std::make_unique<Win32JournalSink>(path);
    if (!sink->valid()) {
        return nullptr;
    }
    return sink;
}

bool is_continuation(const unsigned char value) noexcept { return (value & 0xc0U) == 0x80U; }

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
            if (!is_continuation(next)) {
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

void append_json_string(std::string& output, const std::string_view value) {
    static constexpr char hex[] = "0123456789abcdef";
    output.push_back('"');
    for (const auto raw : value) {
        const auto byte = static_cast<unsigned char>(raw);
        switch (byte) {
            case '"': output += "\\\""; break;
            case '\\': output += "\\\\"; break;
            case '\b': output += "\\b"; break;
            case '\f': output += "\\f"; break;
            case '\n': output += "\\n"; break;
            case '\r': output += "\\r"; break;
            case '\t': output += "\\t"; break;
            default:
                if (byte < 0x20U) {
                    output += "\\u00";
                    output.push_back(hex[byte >> 4U]);
                    output.push_back(hex[byte & 0x0fU]);
                } else {
                    output.push_back(raw);
                }
        }
    }
    output.push_back('"');
}

bool uuid_is_v4(const std::string_view value) noexcept {
    if (value.size() != 36 || value[8] != '-' || value[13] != '-' || value[18] != '-' ||
        value[23] != '-' || value[14] != '4' || (value[19] != '8' && value[19] != '9' &&
                                                   value[19] != 'a' && value[19] != 'b')) {
        return false;
    }
    for (std::size_t index = 0; index < value.size(); ++index) {
        if (index == 8 || index == 13 || index == 18 || index == 23) {
            continue;
        }
        const char item = value[index];
        if (!((item >= '0' && item <= '9') || (item >= 'a' && item <= 'f'))) {
            return false;
        }
    }
    return true;
}

std::string event_name(const EventType type) {
    switch (type) {
        case EventType::recording_started: return "recording_started";
        case EventType::scene_changed: return "scene_changed";
        case EventType::intro_started: return "intro_started";
        case EventType::intro_ended: return "intro_ended";
        case EventType::outro_started: return "outro_started";
        case EventType::outro_ended: return "outro_ended";
        case EventType::stinger_started: return "stinger_started";
        case EventType::stinger_ended: return "stinger_ended";
        case EventType::manual_protection: return "manual_protection";
    }
    return {};
}

ProducerResult result_for_state(const ProducerState state) noexcept {
    switch (state) {
        case ProducerState::producer_failed_queue_overflow:
            return ProducerResult::producer_failed_queue_overflow;
        case ProducerState::producer_failed_io: return ProducerResult::producer_failed_io;
        case ProducerState::producer_failed_shutdown_timeout:
            return ProducerResult::producer_shutdown_timeout;
        case ProducerState::producer_failed_internal: return ProducerResult::producer_internal_error;
        default: return ProducerResult::producer_ok;
    }
}

void detach_noexcept(std::thread& thread) noexcept {
    try {
        if (thread.joinable()) {
            thread.detach();
        }
    } catch (...) {
    }
}

}  // namespace

struct JournalProducer::Shared final {
    explicit Shared(ProducerOptions value)
        : options(std::move(value)), slots(options.queue_capacity) {}

    ProducerOptions options;
    std::atomic<ProducerState> state{ProducerState::not_started};
    std::atomic<ProducerResult> stable_result{ProducerResult::producer_ok};
    std::mutex producer_mutex;
    std::mutex writer_wait_mutex;
    std::condition_variable queue_changed;
    std::vector<std::optional<JournalSnapshot>> slots;
    std::atomic<std::uint64_t> read_position{};
    std::atomic<std::uint64_t> write_position{};
    std::atomic<std::uint64_t> durable_position{};
    std::atomic<WriterFailure> writer_failure{WriterFailure::none};
    std::atomic<std::uint64_t> failure_monotonic_ns{};
    std::atomic<std::uint64_t> failure_output_frame_count{};
    std::atomic<std::uint64_t> failure_pause_counter{};
    std::mutex progress_mutex;
    std::condition_variable progress_changed;
    std::optional<RecordingStop> stop;
    std::unique_ptr<JournalSink> sink;
    std::string session_id;
    RecordingStart start;
    std::mutex lifecycle_mutex;
    std::condition_variable lifecycle_changed;
    bool startup_done{};
    bool writer_done{};

    CallbackResult try_push(JournalSnapshot&& snapshot) noexcept {
        std::unique_lock lock(producer_mutex, std::try_to_lock);
        if (!lock.owns_lock()) {
            return CallbackResult::internal_error;
        }
        if (state.load(std::memory_order_acquire) != ProducerState::recording_active) {
            return CallbackResult::terminal;
        }
        if (const auto* event = std::get_if<EventSnapshot>(&snapshot);
            event != nullptr &&
            (!uuid_is_v4(event->event_id) ||
             (event->source_uuid.has_value() && !uuid_is_v4(*event->source_uuid)) ||
             (event->pair_id.has_value() && !uuid_is_v4(*event->pair_id)) ||
             (event->label.has_value() && event->label->size() > 500))) {
            return CallbackResult::internal_error;
        }
        if (const auto* pause = std::get_if<PauseSnapshot>(&snapshot);
            pause != nullptr && (!uuid_is_v4(pause->event_id) || !pause->clock.recording_paused)) {
            return CallbackResult::internal_error;
        }
        if (const auto* resume = std::get_if<ResumeSnapshot>(&snapshot);
            resume != nullptr && (!uuid_is_v4(resume->event_id) || resume->clock.recording_paused)) {
            return CallbackResult::internal_error;
        }
        const auto write = write_position.load(std::memory_order_relaxed);
        const auto read = read_position.load(std::memory_order_acquire);
        if (write - read >= slots.size()) {
            return CallbackResult::full;
        }
        try {
            slots[static_cast<std::size_t>(write % slots.size())].emplace(
                std::move(snapshot));
        } catch (...) {
            return CallbackResult::internal_error;
        }
        write_position.store(write + 1, std::memory_order_release);
        lock.unlock();
        queue_changed.notify_one();
        return CallbackResult::accepted;
    }
};

class JournalProducer::ThreadHolder final {
  public:
    std::thread value;
};

namespace {

void set_primary_failure(
    const std::shared_ptr<JournalProducer::Shared>& shared,
    const ProducerState failure,
    const ProducerResult result) noexcept {
    auto current = shared->state.load(std::memory_order_acquire);
    while (current == ProducerState::not_started ||
           current == ProducerState::recording_active ||
           current == ProducerState::stop_requested) {
        if (shared->state.compare_exchange_weak(
                current, failure, std::memory_order_acq_rel, std::memory_order_acquire)) {
            shared->stable_result.store(result, std::memory_order_release);
            shared->queue_changed.notify_one();
            shared->progress_changed.notify_all();
            return;
        }
    }
}

void set_writer_failure(
    const std::shared_ptr<JournalProducer::Shared>& shared,
    const WriterFailure failure,
    const ClockSnapshot& clock = {},
    const std::uint64_t pause_counter = 0) noexcept {
    shared->writer_failure.store(failure, std::memory_order_release);
    shared->failure_monotonic_ns.store(clock.monotonic_ns, std::memory_order_release);
    shared->failure_output_frame_count.store(
        clock.output_frame_count, std::memory_order_release);
    shared->failure_pause_counter.store(pause_counter, std::memory_order_release);
}

std::string header_line(const JournalProducer::Shared& shared) {
    std::string result;
    result.reserve(512 + shared.start.recording_path_utf8.size());
    result += "{\"artifact_type\":\"recording_event_journal\",\"capabilities\":{\"file_splitting\":\"unsupported_v1\",\"pause_resume\":\"supported_v1\"},\"clock\":{\"origin\":\"producer_monotonic_at_output_start_signal\",\"source\":\"windows_qpc\",\"unit\":\"ns\"},\"initial_output_path\":";
    append_json_string(result, shared.start.recording_path_utf8);
    result += ",\"journal_schema_version\":\"1.0\",\"lifecycle_status\":\"recording\",\"producer\":{\"name\":\"matrix-auto-cutter-obs-producer\",\"obs_version\":";
    append_json_string(result, shared.start.obs_version);
    result += ",\"version\":";
    append_json_string(result, shared.start.producer_version);
    result += "},\"record_type\":\"header\",\"recording_session_id\":";
    append_json_string(result, shared.session_id);
    result += ",\"sequence\":0}";
    return result;
}

std::string event_line(const EventSnapshot& event, const std::uint64_t sequence) {
    std::string result;
    result.reserve(384 + event.label.value_or("").size());
    result += "{\"artifact_type\":\"recording_event_journal\",\"event_id\":";
    append_json_string(result, event.event_id);
    result += ",\"event_type\":";
    append_json_string(result, event_name(event.event_type));
    result += ",\"journal_schema_version\":\"1.0\"";
    if (event.label.has_value()) {
        result += ",\"label\":";
        append_json_string(result, *event.label);
    }
    result += ",\"monotonic_ns\":" + std::to_string(event.clock.monotonic_ns);
    result += ",\"output_frame_count\":" + std::to_string(event.clock.output_frame_count);
    if (event.pair_id.has_value()) {
        result += ",\"pair_id\":";
        append_json_string(result, *event.pair_id);
    }
    result += ",\"record_type\":\"event\",\"recording_paused\":";
    result += event.clock.recording_paused ? "true" : "false";
    result += ",\"sequence\":" + std::to_string(sequence);
    if (event.source_uuid.has_value()) {
        result += ",\"source_uuid\":";
        append_json_string(result, *event.source_uuid);
    }
    result += "}";
    return result;
}

std::string calibration_line(const CalibrationSnapshot& sample, const std::uint64_t sequence) {
    return "{\"artifact_type\":\"recording_event_journal\",\"journal_schema_version\":\"1.0\",\"monotonic_ns\":" +
           std::to_string(sample.clock.monotonic_ns) + ",\"output_frame_count\":" +
           std::to_string(sample.clock.output_frame_count) +
           ",\"record_type\":\"calibration_sample\",\"recording_paused\":false,\"sequence\":" +
           std::to_string(sequence) + "}";
}

std::string pause_line(const PauseSnapshot& pause, const std::uint64_t sequence) {
    std::string result =
        "{\"artifact_type\":\"recording_event_journal\",\"event_id\":";
    append_json_string(result, pause.event_id);
    result += ",\"journal_schema_version\":\"1.0\",\"monotonic_ns\":" +
              std::to_string(pause.clock.monotonic_ns) + ",\"output_frame_count\":" +
              std::to_string(pause.clock.output_frame_count) +
              ",\"record_type\":\"pause\",\"recording_paused\":true,\"sequence\":" +
              std::to_string(sequence) + "}";
    return result;
}

std::string resume_line(const ResumeSnapshot& resume, const std::uint64_t sequence) {
    std::string result =
        "{\"artifact_type\":\"recording_event_journal\",\"event_id\":";
    append_json_string(result, resume.event_id);
    result += ",\"journal_schema_version\":\"1.0\",\"monotonic_ns\":" +
              std::to_string(resume.clock.monotonic_ns) + ",\"output_frame_count\":" +
              std::to_string(resume.clock.output_frame_count) +
              ",\"record_type\":\"resume\",\"recording_paused\":false,\"sequence\":" +
              std::to_string(sequence) + "}";
    return result;
}

std::string stop_line(const RecordingStop& stop, const std::uint64_t sequence) {
    std::string result =
        "{\"artifact_type\":\"recording_event_journal\",\"file_splitting_detected\":false,\"journal_schema_version\":\"1.0\",\"last_recording_path\":";
    append_json_string(result, stop.recording_path_utf8);
    result += ",\"lifecycle_status\":\"stopped_unfinalized\",\"monotonic_ns\":" +
              std::to_string(stop.clock.monotonic_ns) + ",\"output_frame_count\":" +
              std::to_string(stop.clock.output_frame_count) +
              ",\"output_result\":\"success\",\"record_type\":\"stop\",\"recording_paused\":";
    result += stop.clock.recording_paused ? "true" : "false";
    result += ",\"sequence\":" + std::to_string(sequence) + "}";
    return result;
}

bool clock_valid(
    const ClockSnapshot& clock,
    const std::optional<ClockSnapshot>& previous,
    const bool require_active) noexcept {
    if ((require_active && clock.recording_paused) ||
        (previous.has_value() &&
         (clock.monotonic_ns < previous->monotonic_ns ||
          clock.output_frame_count < previous->output_frame_count))) {
        return false;
    }
    return true;
}

bool snapshot_valid(const JournalSnapshot& snapshot) noexcept {
    if (const auto* event = std::get_if<EventSnapshot>(&snapshot)) {
        return !event->clock.recording_paused && uuid_is_v4(event->event_id) &&
               (!event->source_uuid.has_value() || uuid_is_v4(*event->source_uuid)) &&
               (!event->pair_id.has_value() || uuid_is_v4(*event->pair_id)) &&
               (!event->label.has_value() ||
                (event->label->size() <= 500 && valid_utf8(*event->label)));
    }
    if (const auto* sample = std::get_if<CalibrationSnapshot>(&snapshot)) {
        return !sample->clock.recording_paused;
    }
    if (const auto* pause = std::get_if<PauseSnapshot>(&snapshot)) {
        return uuid_is_v4(pause->event_id) && pause->clock.recording_paused;
    }
    const auto& resume = std::get<ResumeSnapshot>(snapshot);
    return uuid_is_v4(resume.event_id) && !resume.clock.recording_paused;
}

bool event_id_is_unique(
    const JournalSnapshot& snapshot,
    std::unordered_set<std::string>& event_ids) noexcept {
    try {
        if (const auto* event = std::get_if<EventSnapshot>(&snapshot)) {
            return event_ids.emplace(event->event_id).second;
        }
        if (const auto* pause = std::get_if<PauseSnapshot>(&snapshot)) {
            return event_ids.emplace(pause->event_id).second;
        }
        if (const auto* resume = std::get_if<ResumeSnapshot>(&snapshot)) {
            return event_ids.emplace(resume->event_id).second;
        }
        return true;
    } catch (...) {
        return false;
    }
}

void finish_writer(const std::shared_ptr<JournalProducer::Shared>& shared) noexcept {
    shared->sink.reset();
    {
        std::lock_guard lock(shared->lifecycle_mutex);
        shared->writer_done = true;
    }
    shared->lifecycle_changed.notify_all();
}

void writer_main(const std::shared_ptr<JournalProducer::Shared>& shared) noexcept {
    try {
        if (!shared->sink->write_line(header_line(*shared))) {
            set_primary_failure(
                shared, ProducerState::producer_failed_io, ProducerResult::producer_failed_io);
        } else {
            auto expected = ProducerState::not_started;
            if (!shared->state.compare_exchange_strong(
                    expected,
                    ProducerState::recording_active,
                    std::memory_order_acq_rel,
                    std::memory_order_acquire)) {
                set_primary_failure(
                    shared,
                    ProducerState::producer_failed_internal,
                    ProducerResult::producer_internal_error);
            }
        }
    } catch (...) {
        set_primary_failure(
            shared, ProducerState::producer_failed_internal, ProducerResult::producer_internal_error);
    }
    {
        std::lock_guard lock(shared->lifecycle_mutex);
        shared->startup_done = true;
    }
    shared->lifecycle_changed.notify_all();

    std::uint64_t sequence = 1;
    std::optional<ClockSnapshot> previous;
    std::unordered_set<std::string> event_ids;
    bool paused = false;
    std::uint64_t pause_counter = 0;
    while (shared->state.load(std::memory_order_acquire) == ProducerState::recording_active ||
           shared->state.load(std::memory_order_acquire) == ProducerState::stop_requested ||
           shared->state.load(std::memory_order_acquire) ==
               ProducerState::producer_failed_queue_overflow) {
        std::optional<JournalSnapshot> item;
        std::optional<RecordingStop> stop;
        const auto read = shared->read_position.load(std::memory_order_relaxed);
        const auto write = shared->write_position.load(std::memory_order_acquire);
        if (read < write) {
            const auto slot = static_cast<std::size_t>(read % shared->slots.size());
            item.emplace(std::move(*shared->slots[slot]));
            shared->slots[slot].reset();
            shared->read_position.store(read + 1, std::memory_order_release);
        } else if (shared->state.load(std::memory_order_acquire) ==
                   ProducerState::stop_requested) {
            stop = shared->stop;
        } else if (shared->state.load(std::memory_order_acquire) ==
                   ProducerState::recording_active) {
            std::unique_lock lock(shared->writer_wait_mutex);
            shared->queue_changed.wait_for(lock, std::chrono::milliseconds(10), [&] {
                return shared->read_position.load(std::memory_order_relaxed) <
                           shared->write_position.load(std::memory_order_acquire) ||
                       shared->state.load(std::memory_order_acquire) !=
                           ProducerState::recording_active;
            });
            continue;
        } else {
            break;
        }
        if (item.has_value()) {
            const auto clock = std::visit([](const auto& value) { return value.clock; }, *item);
            const bool is_pause = std::holds_alternative<PauseSnapshot>(*item);
            const bool is_resume = std::holds_alternative<ResumeSnapshot>(*item);
            const bool requires_active = std::holds_alternative<EventSnapshot>(*item) ||
                                         std::holds_alternative<CalibrationSnapshot>(*item);
            WriterFailure failure = WriterFailure::none;
            if (!snapshot_valid(*item)) {
                failure = WriterFailure::snapshot_invalid;
            } else if (previous.has_value() &&
                       clock.monotonic_ns < previous->monotonic_ns) {
                failure = WriterFailure::qpc_regression;
            } else if (previous.has_value() &&
                       clock.output_frame_count < previous->output_frame_count) {
                failure = is_resume ? WriterFailure::resume_counter_underflow
                                    : WriterFailure::counter_regression;
            } else if (is_pause && paused) {
                failure = WriterFailure::pause_while_paused;
            } else if (is_resume && !paused) {
                failure = WriterFailure::resume_while_active;
            } else if (is_resume && clock.output_frame_count < pause_counter) {
                failure = WriterFailure::resume_counter_underflow;
                // OBS 32.1.2 increments total_frames when already-encoded packets leave
                // its A/V interleaver. That backlog may drain after the pause signal, so
                // monotonicity is the only configuration-independent upper contract.
            } else if (requires_active && paused) {
                failure = WriterFailure::active_snapshot_while_paused;
            } else if (!clock_valid(clock, previous, requires_active)) {
                failure = WriterFailure::snapshot_invalid;
            } else if (!event_id_is_unique(*item, event_ids)) {
                failure = WriterFailure::duplicate_event_id;
            }
            if (failure != WriterFailure::none) {
                set_writer_failure(shared, failure, clock, pause_counter);
                set_primary_failure(
                    shared,
                    ProducerState::producer_failed_internal,
                    ProducerResult::producer_internal_error);
                continue;
            }
            std::optional<std::string> line;
            try {
                line.emplace(std::visit(
                    [&](const auto& value) {
                        using Value = std::decay_t<decltype(value)>;
                        if constexpr (std::is_same_v<Value, EventSnapshot>) {
                            return event_line(value, sequence);
                        } else if constexpr (std::is_same_v<Value, CalibrationSnapshot>) {
                            return calibration_line(value, sequence);
                        } else if constexpr (std::is_same_v<Value, PauseSnapshot>) {
                            return pause_line(value, sequence);
                        } else {
                            return resume_line(value, sequence);
                        }
                    },
                    *item));
            } catch (...) {
                set_writer_failure(
                    shared, WriterFailure::serialization_failed, clock, pause_counter);
                set_primary_failure(
                    shared,
                    ProducerState::producer_failed_internal,
                    ProducerResult::producer_internal_error);
                continue;
            }
            if (!shared->sink->write_line(*line)) {
                set_writer_failure(
                    shared, WriterFailure::write_or_flush_failed, clock, pause_counter);
                set_primary_failure(
                    shared, ProducerState::producer_failed_io, ProducerResult::producer_failed_io);
                continue;
            }
            shared->durable_position.store(read + 1, std::memory_order_release);
            shared->progress_changed.notify_all();
            ++sequence;
            previous = clock;
            if (is_pause) {
                paused = true;
                pause_counter = clock.output_frame_count;
            } else if (is_resume) {
                paused = false;
            }
            continue;
        }
        if (!stop.has_value() || stop->clock.recording_paused != paused ||
            !clock_valid(stop->clock, previous, !paused) ||
            stop->recording_path_utf8 != shared->start.recording_path_utf8) {
            set_primary_failure(
                shared,
                ProducerState::producer_failed_internal,
                ProducerResult::producer_internal_error);
            break;
        }
        bool stop_written = false;
        try {
            stop_written = shared->sink->write_line(stop_line(*stop, sequence));
        } catch (...) {
            stop_written = false;
        }
        if (!stop_written) {
            set_primary_failure(
                shared, ProducerState::producer_failed_io, ProducerResult::producer_failed_io);
            break;
        }
        auto expected = ProducerState::stop_requested;
        shared->state.compare_exchange_strong(
            expected,
            ProducerState::stopped_unfinalized,
            std::memory_order_acq_rel,
            std::memory_order_acquire);
        break;
    }
    finish_writer(shared);
}

}  // namespace

JournalProducer::JournalProducer(ProducerOptions options)
    : thread_(std::make_unique<ThreadHolder>()) {
    if (options.queue_capacity == 0) {
        options.queue_capacity = 1;
    }
    if (!options.sink_factory) {
        options.sink_factory = default_sink_factory;
    }
    if (!options.uuid_factory) {
        options.uuid_factory = uuid_v4;
    }
    if (!options.shutdown_clock) {
        options.shutdown_clock = std::make_shared<SystemShutdownClock>();
    }
    if (!options.shutdown_wait) {
        options.shutdown_wait = std::make_shared<SystemShutdownWait>();
    }
    shared_ = std::make_shared<Shared>(std::move(options));
}

JournalProducer::~JournalProducer() {
    static_cast<void>(shutdown());
}

ProducerResult JournalProducer::start_recording(const RecordingStart& start) noexcept {
    try {
        if (shared_->state.load(std::memory_order_acquire) != ProducerState::not_started ||
            start.journal_path.empty() || start.recording_path_utf8.empty() ||
            start.producer_version.empty() || start.obs_version.empty() ||
            !valid_utf8(start.recording_path_utf8) || !valid_utf8(start.producer_version) ||
            !valid_utf8(start.obs_version)) {
            return ProducerResult::producer_internal_error;
        }
        if (start.recording_session_id.has_value()) {
            if (!uuid_is_v4(*start.recording_session_id)) {
                set_primary_failure(
                    shared_,
                    ProducerState::producer_failed_internal,
                    ProducerResult::producer_internal_error);
                return ProducerResult::producer_internal_error;
            }
            const auto expected_name =
                *start.recording_session_id + ".recording-journal.ndjson";
            if (start.journal_path.filename() != std::filesystem::path(expected_name)) {
                set_primary_failure(
                    shared_,
                    ProducerState::producer_failed_internal,
                    ProducerResult::producer_internal_error);
                return ProducerResult::producer_internal_error;
            }
            shared_->session_id = *start.recording_session_id;
        } else {
            shared_->session_id = shared_->options.uuid_factory();
        }
        shared_->start = start;
        if (!uuid_is_v4(shared_->session_id)) {
            set_primary_failure(
                shared_,
                ProducerState::producer_failed_internal,
                ProducerResult::producer_internal_error);
            return ProducerResult::producer_internal_error;
        }
        shared_->sink = shared_->options.sink_factory(start.journal_path);
        if (!shared_->sink) {
            set_primary_failure(
                shared_, ProducerState::producer_failed_io, ProducerResult::producer_failed_io);
            return ProducerResult::producer_failed_io;
        }
        thread_->value = std::thread([shared = shared_]() mutable noexcept {
            auto writer_thread_exit = std::move(shared->options.writer_thread_exit);
            writer_main(shared);
            // A timed-out plugin host may already have released its producer.
            // Drop the writer's final shared-state reference before the host's
            // non-returning module-unpin/exit operation.
            shared.reset();
            if (writer_thread_exit) {
                try {
                    writer_thread_exit();
                } catch (...) {
                }
            }
        });
        std::unique_lock lock(shared_->lifecycle_mutex);
        shared_->lifecycle_changed.wait(lock, [&] { return shared_->startup_done; });
        return result();
    } catch (...) {
        set_primary_failure(
            shared_, ProducerState::producer_failed_internal, ProducerResult::producer_internal_error);
        return ProducerResult::producer_internal_error;
    }
}

CallbackResult JournalProducer::submit(JournalSnapshot snapshot) noexcept {
    try {
        const CallbackResult outcome = shared_->try_push(std::move(snapshot));
        if (outcome == CallbackResult::full) {
            auto expected = ProducerState::recording_active;
            if (shared_->state.compare_exchange_strong(
                    expected,
                    ProducerState::producer_failed_queue_overflow,
                    std::memory_order_acq_rel,
                    std::memory_order_acquire)) {
                shared_->stable_result.store(
                    ProducerResult::producer_failed_queue_overflow, std::memory_order_release);
                shared_->queue_changed.notify_one();
                return CallbackResult::full;
            }
            return CallbackResult::terminal;
        }
        if (outcome == CallbackResult::internal_error) {
            auto expected = ProducerState::recording_active;
            if (shared_->state.compare_exchange_strong(
                    expected,
                    ProducerState::producer_failed_internal,
                    std::memory_order_acq_rel,
                    std::memory_order_acquire)) {
                shared_->stable_result.store(
                    ProducerResult::producer_internal_error, std::memory_order_release);
                shared_->queue_changed.notify_one();
                return CallbackResult::internal_error;
            }
            return CallbackResult::terminal;
        }
        return outcome;
    } catch (...) {
        set_primary_failure(
            shared_, ProducerState::producer_failed_internal, ProducerResult::producer_internal_error);
        return CallbackResult::internal_error;
    }
}

ProducerResult JournalProducer::confirm_durable() noexcept {
    try {
        const auto target = shared_->write_position.load(std::memory_order_acquire);
        std::unique_lock lock(shared_->progress_mutex);
        const bool completed = shared_->progress_changed.wait_for(
            lock, producer_durable_confirmation_deadline, [&] {
                return shared_->durable_position.load(std::memory_order_acquire) >= target ||
                       shared_->state.load(std::memory_order_acquire) !=
                           ProducerState::recording_active;
            });
        lock.unlock();
        if (shared_->durable_position.load(std::memory_order_acquire) >= target) {
            return result();
        }
        if (!completed) {
            set_writer_failure(shared_, WriterFailure::durable_confirmation_timeout);
            set_primary_failure(
                shared_,
                ProducerState::producer_failed_internal,
                ProducerResult::producer_internal_error);
        }
        const auto stable = result();
        return stable == ProducerResult::producer_ok ? ProducerResult::producer_internal_error
                                                     : stable;
    } catch (...) {
        set_primary_failure(
            shared_, shared_->state.load(std::memory_order_acquire) ==
                             ProducerState::producer_failed_io
                         ? ProducerState::producer_failed_io
                         : ProducerState::producer_failed_internal,
            shared_->state.load(std::memory_order_acquire) == ProducerState::producer_failed_io
                ? ProducerResult::producer_failed_io
                : ProducerResult::producer_internal_error);
        return result();
    }
}

ProducerResult JournalProducer::normal_stop(const RecordingStop& stop) noexcept {
    try {
        if (stop.recording_path_utf8.empty() || !valid_utf8(stop.recording_path_utf8) ||
            stop.recording_path_utf8 != shared_->start.recording_path_utf8) {
            set_primary_failure(
                shared_,
                ProducerState::producer_failed_internal,
                ProducerResult::producer_internal_error);
            return ProducerResult::producer_internal_error;
        }
        std::unique_lock lock(shared_->producer_mutex);
        if (shared_->state.load(std::memory_order_acquire) != ProducerState::recording_active) {
            const auto current = result();
            return current == ProducerResult::producer_ok
                       ? ProducerResult::producer_rejected_after_stop
                       : current;
        }
        shared_->stop = stop;
        auto expected = ProducerState::recording_active;
        if (!shared_->state.compare_exchange_strong(
                expected,
                ProducerState::stop_requested,
                std::memory_order_acq_rel,
                std::memory_order_acquire)) {
            shared_->stop.reset();
            const auto current = result();
            return current == ProducerResult::producer_ok
                       ? ProducerResult::producer_rejected_after_stop
                       : current;
        }
        lock.unlock();
        shared_->queue_changed.notify_one();
        return ProducerResult::producer_ok;
    } catch (...) {
        set_primary_failure(
            shared_, ProducerState::producer_failed_internal, ProducerResult::producer_internal_error);
        return ProducerResult::producer_internal_error;
    }
}

ProducerResult JournalProducer::shutdown() noexcept {
    std::lock_guard shutdown_lock(shutdown_mutex_);
    if (shutdown_called_) {
        return shutdown_result_;
    }
    shutdown_called_ = true;
    try {
        auto current = shared_->state.load(std::memory_order_acquire);
        if (current == ProducerState::not_started) {
            shared_->state.compare_exchange_strong(
                current,
                ProducerState::closed,
                std::memory_order_acq_rel,
                std::memory_order_acquire);
        } else if (current == ProducerState::recording_active) {
            if (shared_->state.compare_exchange_strong(
                    current,
                    ProducerState::producer_failed_internal,
                    std::memory_order_acq_rel,
                    std::memory_order_acquire)) {
                shared_->stable_result.store(
                    ProducerResult::producer_internal_error, std::memory_order_release);
            }
        }
        shared_->queue_changed.notify_one();
        if (thread_->value.joinable()) {
            std::unique_lock lock(shared_->lifecycle_mutex);
            const auto deadline = shared_->options.shutdown_clock->now() + producer_shutdown_deadline;
            const bool done = shared_->options.shutdown_wait->wait_until(
                shared_->lifecycle_changed, lock, deadline, [&] { return shared_->writer_done; });
            lock.unlock();
            if (!done) {
                auto timeout_expected = shared_->state.load(std::memory_order_acquire);
                while (timeout_expected == ProducerState::producer_failed_internal ||
                       timeout_expected == ProducerState::stop_requested ||
                       timeout_expected == ProducerState::recording_active) {
                    if (shared_->state.compare_exchange_weak(
                            timeout_expected,
                            ProducerState::producer_failed_shutdown_timeout,
                            std::memory_order_acq_rel,
                            std::memory_order_acquire)) {
                        break;
                    }
                }
                detach_noexcept(thread_->value);
                shutdown_result_ = ProducerResult::producer_shutdown_timeout;
                return shutdown_result_;
            }
            thread_->value.join();
        }
        if (shared_->state.load(std::memory_order_acquire) == ProducerState::stopped_unfinalized) {
            shared_->state.store(ProducerState::closed, std::memory_order_release);
        }
        shutdown_result_ = result();
        return shutdown_result_;
    } catch (...) {
        detach_noexcept(thread_->value);
        shutdown_result_ = ProducerResult::producer_internal_error;
        return shutdown_result_;
    }
}

ProducerResult JournalProducer::result() const noexcept {
    const auto stable = shared_->stable_result.load(std::memory_order_acquire);
    return stable == ProducerResult::producer_ok
               ? result_for_state(shared_->state.load(std::memory_order_acquire))
               : stable;
}

ProducerState JournalProducer::state() const noexcept {
    return shared_->state.load(std::memory_order_acquire);
}

ProducerStatus JournalProducer::status() const noexcept {
    return ProducerStatus{
        shared_->state.load(std::memory_order_acquire),
        result(),
        shared_->read_position.load(std::memory_order_acquire),
        shared_->write_position.load(std::memory_order_acquire),
        shared_->durable_position.load(std::memory_order_acquire),
        shared_->writer_failure.load(std::memory_order_acquire),
        shared_->failure_monotonic_ns.load(std::memory_order_acquire),
        shared_->failure_output_frame_count.load(std::memory_order_acquire),
        shared_->failure_pause_counter.load(std::memory_order_acquire),
    };
}

std::string JournalProducer::recording_session_id() const noexcept {
    try {
        return shared_->session_id;
    } catch (...) {
        return {};
    }
}

MonotonicQpcClock::MonotonicQpcClock() noexcept {
    LARGE_INTEGER origin{};
    LARGE_INTEGER frequency{};
    if (QueryPerformanceCounter(&origin) != 0 && QueryPerformanceFrequency(&frequency) != 0) {
        origin_ = origin.QuadPart;
        frequency_ = frequency.QuadPart;
    }
}

std::uint64_t MonotonicQpcClock::now_ns() const noexcept {
    LARGE_INTEGER current{};
    if (frequency_ <= 0 || QueryPerformanceCounter(&current) == 0 || current.QuadPart < origin_) {
        return 0;
    }
    const auto elapsed = static_cast<std::uint64_t>(current.QuadPart - origin_);
    const auto frequency = static_cast<std::uint64_t>(frequency_);
    return (elapsed / frequency) * 1'000'000'000ULL +
           ((elapsed % frequency) * 1'000'000'000ULL) / frequency;
}

std::string uuid_v4() noexcept {
    std::array<unsigned char, 16> bytes{};
    if (BCryptGenRandom(nullptr, bytes.data(), static_cast<ULONG>(bytes.size()),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0) {
        return {};
    }
    bytes[6] = static_cast<unsigned char>((bytes[6] & 0x0fU) | 0x40U);
    bytes[8] = static_cast<unsigned char>((bytes[8] & 0x3fU) | 0x80U);
    std::array<char, 37> output{};
    std::snprintf(
        output.data(),
        output.size(),
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
        bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]);
    return output.data();
}

bool valid_uuid_v4(const std::string_view value) noexcept { return uuid_is_v4(value); }

const char* to_string(const CallbackResult value) noexcept {
    switch (value) {
        case CallbackResult::accepted: return "accepted";
        case CallbackResult::full: return "full";
        case CallbackResult::terminal: return "terminal";
        case CallbackResult::internal_error: return "internal_error";
    }
    return "internal_error";
}

const char* to_string(const ProducerResult value) noexcept {
    switch (value) {
        case ProducerResult::producer_ok: return "producer_ok";
        case ProducerResult::producer_rejected_after_stop:
            return "producer_rejected_after_stop";
        case ProducerResult::producer_failed_queue_overflow:
            return "producer_failed_queue_overflow";
        case ProducerResult::producer_failed_io: return "producer_failed_io";
        case ProducerResult::producer_shutdown_timeout: return "producer_shutdown_timeout";
        case ProducerResult::producer_internal_error: return "producer_internal_error";
    }
    return "producer_internal_error";
}

const char* to_string(const ProducerState value) noexcept {
    switch (value) {
        case ProducerState::not_started: return "not_started";
        case ProducerState::recording_active: return "recording_active";
        case ProducerState::stop_requested: return "stop_requested";
        case ProducerState::producer_failed_queue_overflow:
            return "producer_failed_queue_overflow";
        case ProducerState::producer_failed_io: return "producer_failed_io";
        case ProducerState::producer_failed_internal: return "producer_failed_internal";
        case ProducerState::producer_failed_shutdown_timeout:
            return "producer_failed_shutdown_timeout";
        case ProducerState::stopped_unfinalized: return "stopped_unfinalized";
        case ProducerState::closed: return "closed";
    }
    return "producer_failed_internal";
}

const char* to_string(const WriterFailure value) noexcept {
    switch (value) {
        case WriterFailure::none: return "none";
        case WriterFailure::snapshot_invalid: return "snapshot_invalid";
        case WriterFailure::qpc_regression: return "qpc_regression";
        case WriterFailure::counter_regression: return "counter_regression";
        case WriterFailure::pause_while_paused: return "pause_while_paused";
        case WriterFailure::resume_while_active: return "resume_while_active";
        case WriterFailure::resume_counter_underflow: return "resume_counter_underflow";
        case WriterFailure::active_snapshot_while_paused:
            return "active_snapshot_while_paused";
        case WriterFailure::duplicate_event_id: return "duplicate_event_id";
        case WriterFailure::serialization_failed: return "serialization_failed";
        case WriterFailure::write_or_flush_failed: return "write_or_flush_failed";
        case WriterFailure::durable_confirmation_timeout:
            return "durable_confirmation_timeout";
    }
    return "snapshot_invalid";
}

}  // namespace matrix_auto_cutter
