#include "matrix_auto_cutter/journal_producer.hpp"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using matrix_auto_cutter::CallbackResult;
using matrix_auto_cutter::CalibrationSnapshot;
using matrix_auto_cutter::ClockSnapshot;
using matrix_auto_cutter::EventSnapshot;
using matrix_auto_cutter::EventType;
using matrix_auto_cutter::JournalProducer;
using matrix_auto_cutter::JournalSink;
using matrix_auto_cutter::ProducerOptions;
using matrix_auto_cutter::ProducerResult;
using matrix_auto_cutter::ProducerState;
using matrix_auto_cutter::PauseSnapshot;
using matrix_auto_cutter::RecordingStart;
using matrix_auto_cutter::RecordingStop;
using matrix_auto_cutter::ResumeSnapshot;

constexpr std::string_view session_id = "11111111-1111-4111-8111-111111111111";
constexpr std::string_view start_id = "22222222-2222-4222-8222-222222222222";
constexpr std::string_view recording_path = R"(C:\Media\native.mp4)";

struct SinkState final {
    std::mutex mutex;
    std::condition_variable changed;
    std::vector<std::string> lines;
    bool block_event{};
    bool event_entered{};
    bool release{};
    int fail_at{-1};
    int writer_exit_calls{};
};

class TestSink final : public JournalSink {
  public:
    explicit TestSink(std::shared_ptr<SinkState> state) : state_(std::move(state)) {}

    bool write_line(const std::string_view line) noexcept override {
        try {
            std::unique_lock lock(state_->mutex);
            if (state_->fail_at == static_cast<int>(state_->lines.size())) {
                return false;
            }
            state_->lines.emplace_back(line);
            if (state_->block_event && line.find("\"record_type\":\"event\"") != std::string_view::npos &&
                !state_->event_entered) {
                state_->event_entered = true;
                state_->changed.notify_all();
                state_->changed.wait(lock, [&] { return state_->release; });
            }
            return true;
        } catch (...) {
            return false;
        }
    }

  private:
    std::shared_ptr<SinkState> state_;
};

class ImmediateTimeoutWait final : public matrix_auto_cutter::ShutdownWait {
  public:
    bool wait_until(
        std::condition_variable&,
        std::unique_lock<std::mutex>&,
        std::chrono::steady_clock::time_point,
        const std::function<bool()>&) noexcept override {
        return false;
    }
};

struct TestFailure final : std::runtime_error {
    using std::runtime_error::runtime_error;
};

void check(const bool condition, const std::string_view message) {
    if (!condition) {
        throw TestFailure(std::string(message));
    }
}

ProducerOptions options_for(
    const std::shared_ptr<SinkState>& state,
    const std::size_t capacity = 16) {
    ProducerOptions options;
    options.queue_capacity = capacity;
    options.uuid_factory = [] { return std::string(session_id); };
    options.sink_factory = [state](const std::filesystem::path&) {
        return std::make_unique<TestSink>(state);
    };
    return options;
}

RecordingStart start_request() {
    return RecordingStart{
        L"ignored.recording-journal.ndjson",
        std::string(recording_path),
        "native-test",
        "standalone-test",
    };
}

EventSnapshot start_event() {
    return EventSnapshot{
        std::string(start_id),
        EventType::recording_started,
        ClockSnapshot{0, 0, false},
        std::nullopt,
        std::nullopt,
        std::nullopt,
    };
}

CalibrationSnapshot sample(const std::uint64_t seconds) {
    return CalibrationSnapshot{ClockSnapshot{seconds * 1'000'000'000ULL, seconds * 60, false}};
}

RecordingStop stop_request(const std::uint64_t seconds = 6) {
    return RecordingStop{
        ClockSnapshot{seconds * 1'000'000'000ULL, seconds * 60, false},
        std::string(recording_path),
    };
}

PauseSnapshot pause_snapshot(const std::uint64_t seconds = 2, const std::uint64_t frames = 120) {
    return PauseSnapshot{
        "33333333-3333-4333-8333-333333333333", ClockSnapshot{seconds * 1'000'000'000ULL, frames, true}};
}

ResumeSnapshot resume_snapshot(const std::uint64_t seconds = 4, const std::uint64_t frames = 121) {
    return ResumeSnapshot{
        "44444444-4444-4444-8444-444444444444", ClockSnapshot{seconds * 1'000'000'000ULL, frames, false}};
}

void wait_for_block(const std::shared_ptr<SinkState>& state) {
    std::unique_lock lock(state->mutex);
    check(state->changed.wait_for(lock, 2s, [&] { return state->event_entered; }),
          "writer did not enter blocking sink");
}

void release_writer(const std::shared_ptr<SinkState>& state) {
    {
        std::lock_guard lock(state->mutex);
        state->release = true;
    }
    state->changed.notify_all();
}

void test_normal_start_snapshot_stop_shutdown_and_exact_lines() {
    auto state = std::make_shared<SinkState>();
    JournalProducer producer(options_for(state));
    check(producer.start_recording(start_request()) == ProducerResult::producer_ok, "start failed");
    check(producer.submit(start_event()) == CallbackResult::accepted, "start event rejected");
    check(producer.submit(sample(2)) == CallbackResult::accepted, "sample 2 rejected");
    check(producer.submit(sample(4)) == CallbackResult::accepted, "sample 4 rejected");
    check(producer.normal_stop(stop_request()) == ProducerResult::producer_ok, "stop failed");
    check(producer.submit(sample(5)) == CallbackResult::terminal, "post-stop callback accepted");
    check(producer.shutdown() == ProducerResult::producer_ok, "shutdown failed");
    check(producer.shutdown() == ProducerResult::producer_ok, "repeated shutdown changed result");
    check(producer.state() == ProducerState::closed, "normal producer did not close");

    std::lock_guard lock(state->mutex);
    check(state->lines.size() == 5, "normal journal line count differs");
    for (std::size_t sequence = 0; sequence < state->lines.size(); ++sequence) {
        const std::string token = "\"sequence\":" + std::to_string(sequence);
        check(state->lines[sequence].find(token) != std::string::npos, "writer sequence gap");
    }
    check(state->lines.front() ==
              "{\"artifact_type\":\"recording_event_journal\",\"capabilities\":{\"file_splitting\":\"unsupported_v1\",\"pause_resume\":\"supported_v1\"},\"clock\":{\"origin\":\"producer_monotonic_at_output_start_signal\",\"source\":\"windows_qpc\",\"unit\":\"ns\"},\"initial_output_path\":\"C:\\\\Media\\\\native.mp4\",\"journal_schema_version\":\"1.0\",\"lifecycle_status\":\"recording\",\"producer\":{\"name\":\"matrix-auto-cutter-obs-producer\",\"obs_version\":\"standalone-test\",\"version\":\"native-test\"},\"record_type\":\"header\",\"recording_session_id\":\"11111111-1111-4111-8111-111111111111\",\"sequence\":0}",
          "header bytes are not canonical and exact");
    check(state->lines.back().find("\"record_type\":\"stop\"") != std::string::npos,
          "normal journal has no final stop");
}

void test_explicit_session_id_binds_filename_and_header_without_generation() {
    auto state = std::make_shared<SinkState>();
    auto options = options_for(state);
    auto generated = std::make_shared<std::atomic<unsigned>>(0);
    options.uuid_factory = [generated] {
        generated->fetch_add(1, std::memory_order_relaxed);
        return std::string(start_id);
    };
    JournalProducer producer(std::move(options));
    auto start = start_request();
    start.recording_session_id = std::string(session_id);
    start.journal_path = std::filesystem::path(
        std::string(session_id) + ".recording-journal.ndjson");
    check(producer.start_recording(start) == ProducerResult::producer_ok,
          "valid explicit session was rejected");
    check(generated->load(std::memory_order_acquire) == 0,
          "core generated a second session id for an explicit session");
    check(producer.recording_session_id() == session_id,
          "explicit session was not retained by the producer");
    check(producer.shutdown() == ProducerResult::producer_internal_error,
          "shutdown without output stop was not fail closed");
    std::lock_guard lock(state->mutex);
    check(!state->lines.empty() &&
              state->lines.front().find(
                  "\"recording_session_id\":\"" + std::string(session_id) + "\"") !=
                  std::string::npos,
          "header did not contain the filename-bound explicit session id");
}

void test_invalid_or_filename_mismatched_explicit_session_is_rejected_before_open() {
    for (const auto& [id, file] : std::vector<std::pair<std::string, std::string>>{
             {"11111111-1111-5111-8111-111111111111",
              "11111111-1111-5111-8111-111111111111.recording-journal.ndjson"},
             {std::string(session_id),
              "33333333-3333-4333-8333-333333333333.recording-journal.ndjson"},
         }) {
        auto state = std::make_shared<SinkState>();
        auto options = options_for(state);
        auto sink_calls = std::make_shared<std::atomic<unsigned>>(0);
        options.sink_factory = [state, sink_calls](const std::filesystem::path&) {
            sink_calls->fetch_add(1, std::memory_order_relaxed);
            return std::make_unique<TestSink>(state);
        };
        JournalProducer producer(std::move(options));
        auto start = start_request();
        start.recording_session_id = id;
        start.journal_path = std::filesystem::path(file);
        check(producer.start_recording(start) == ProducerResult::producer_internal_error,
              "invalid explicit session/path binding was accepted");
        check(sink_calls->load(std::memory_order_acquire) == 0,
              "invalid explicit session reached journal open");
    }
}

void test_existing_explicit_journal_is_not_overwritten() {
    const auto directory = std::filesystem::temp_directory_path() /
                           std::filesystem::path(L"matrix-native-create-new-test");
    std::error_code error;
    std::filesystem::create_directories(directory, error);
    check(!error, "could not create CREATE_NEW test directory");
    const auto journal = directory /
                         std::filesystem::path(
                             std::string(session_id) + ".recording-journal.ndjson");
    const std::string sentinel = "existing-journal-must-survive";
    {
        std::ofstream output(journal, std::ios::binary | std::ios::trunc);
        output << sentinel;
    }
    ProducerOptions options;
    JournalProducer producer(std::move(options));
    auto start = start_request();
    start.recording_session_id = std::string(session_id);
    start.journal_path = journal;
    check(producer.start_recording(start) == ProducerResult::producer_failed_io,
          "existing journal was not rejected by CREATE_NEW");
    std::ifstream input(journal, std::ios::binary);
    const std::string after(
        (std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    check(after == sentinel, "existing journal bytes were overwritten");
    input.close();
    std::filesystem::remove(journal, error);
    std::filesystem::remove(directory, error);
}

struct OverflowFixture final {
    std::shared_ptr<SinkState> sink = std::make_shared<SinkState>();
    JournalProducer producer;

    explicit OverflowFixture(const std::size_t capacity = 2)
        : producer([&] {
              sink->block_event = true;
              return options_for(sink, capacity);
          }()) {
        check(producer.start_recording(start_request()) == ProducerResult::producer_ok,
              "overflow fixture start failed");
        check(producer.submit(start_event()) == CallbackResult::accepted,
              "overflow fixture event rejected");
        wait_for_block(sink);
    }

    ~OverflowFixture() {
        release_writer(sink);
        static_cast<void>(producer.shutdown());
    }
};

void test_queue_full_overflow_single_winner_terminal_and_drain() {
    OverflowFixture fixture;
    check(fixture.producer.submit(sample(1)) == CallbackResult::accepted, "first slot rejected");
    check(fixture.producer.submit(sample(2)) == CallbackResult::accepted, "second slot rejected");
    check(fixture.producer.submit(sample(3)) == CallbackResult::full, "full did not win overflow");
    check(fixture.producer.state() == ProducerState::producer_failed_queue_overflow,
          "overflow state missing");
    check(fixture.producer.submit(sample(4)) == CallbackResult::terminal,
          "second overflow winner existed");
    check(fixture.producer.normal_stop(stop_request()) ==
              ProducerResult::producer_failed_queue_overflow,
          "stop won after overflow");
    release_writer(fixture.sink);
    check(fixture.producer.shutdown() == ProducerResult::producer_failed_queue_overflow,
          "overflow shutdown result changed");
    std::lock_guard lock(fixture.sink->mutex);
    check(fixture.sink->lines.size() == 4, "accepted pre-overflow snapshots were not drained");
    check(fixture.sink->lines.back().find("\"sequence\":3") != std::string::npos,
          "drain order was not contiguous");
    for (const auto& line : fixture.sink->lines) {
        check(line.find("\"record_type\":\"stop\"") == std::string::npos,
              "overflow emitted a stop record");
    }
}

void test_stop_wins_before_full_observation() {
    OverflowFixture fixture;
    check(fixture.producer.submit(sample(1)) == CallbackResult::accepted, "slot one rejected");
    check(fixture.producer.submit(sample(2)) == CallbackResult::accepted, "slot two rejected");
    check(fixture.producer.normal_stop(stop_request()) == ProducerResult::producer_ok,
          "stop did not linearize first");
    check(fixture.producer.submit(sample(3)) == CallbackResult::terminal,
          "post-stop full was misclassified as overflow");
    release_writer(fixture.sink);
    check(fixture.producer.shutdown() == ProducerResult::producer_ok, "winning stop failed");
    std::lock_guard lock(fixture.sink->mutex);
    check(fixture.sink->lines.back().find("\"record_type\":\"stop\"") != std::string::npos,
          "winning stop record missing");
}

void test_write_failure_is_fail_closed() {
    auto state = std::make_shared<SinkState>();
    state->fail_at = 1;
    JournalProducer producer(options_for(state));
    check(producer.start_recording(start_request()) == ProducerResult::producer_ok, "start failed");
    check(producer.submit(start_event()) == CallbackResult::accepted, "event rejected");
    check(producer.normal_stop(stop_request(1)) == ProducerResult::producer_ok,
          "stop request failed before IO failure");
    check(producer.shutdown() == ProducerResult::producer_failed_io, "IO failure not stable");
    check(producer.state() == ProducerState::producer_failed_io, "IO state missing");
}

void test_header_write_or_flush_failure_is_fail_closed() {
    auto state = std::make_shared<SinkState>();
    state->fail_at = 0;
    JournalProducer producer(options_for(state));
    check(producer.start_recording(start_request()) == ProducerResult::producer_failed_io,
          "header write/flush failure was not reported");
    check(producer.shutdown() == ProducerResult::producer_failed_io,
          "header failure changed during shutdown");
}

void test_shutdown_timeout_preserves_live_writer_resources() {
    auto state = std::make_shared<SinkState>();
    state->block_event = true;
    auto options = options_for(state);
    options.shutdown_wait = std::make_shared<ImmediateTimeoutWait>();
    options.writer_thread_exit = [state] {
        {
            std::lock_guard lock(state->mutex);
            ++state->writer_exit_calls;
        }
        state->changed.notify_all();
    };
    {
        JournalProducer producer(std::move(options));
        check(producer.start_recording(start_request()) == ProducerResult::producer_ok,
              "timeout fixture start failed");
        check(producer.submit(start_event()) == CallbackResult::accepted, "timeout event rejected");
        wait_for_block(state);
        check(producer.normal_stop(stop_request(1)) == ProducerResult::producer_ok,
              "timeout stop request did not linearize");
        check(producer.shutdown() == ProducerResult::producer_shutdown_timeout,
              "shutdown timeout was not returned");
        check(producer.shutdown() == ProducerResult::producer_shutdown_timeout,
              "shutdown timeout was not idempotent");
        {
            std::lock_guard lock(state->mutex);
            check(state->writer_exit_calls == 0,
                  "writer module lifetime was released while detached code was still blocked");
        }
    }
    release_writer(state);
    std::unique_lock lock(state->mutex);
    check(state->changed.wait_for(lock, 2s, [&] { return state.use_count() == 1; }),
          "detached writer retained resources after release");
    check(state->writer_exit_calls == 1,
          "writer thread lifetime hook did not run exactly once after timeout release");
    for (const auto& line : state->lines) {
        check(line.find("\"record_type\":\"stop\"") == std::string::npos,
              "timed-out shutdown later emitted a stop");
    }
}

void test_public_boundary_contains_factory_exception() {
    ProducerOptions options;
    options.uuid_factory = [] { return std::string(session_id); };
    options.sink_factory = [](const std::filesystem::path&) -> std::unique_ptr<JournalSink> {
        throw std::runtime_error("injected factory exception");
    };
    JournalProducer producer(std::move(options));
    ProducerResult result = ProducerResult::producer_ok;
    try {
        result = producer.start_recording(start_request());
    } catch (...) {
        throw TestFailure("exception escaped public start boundary");
    }
    check(result == ProducerResult::producer_internal_error, "factory exception was misclassified");
}

void test_unbounded_callback_snapshot_fails_without_escape() {
    auto state = std::make_shared<SinkState>();
    JournalProducer producer(options_for(state));
    check(producer.start_recording(start_request()) == ProducerResult::producer_ok, "start failed");
    auto event = start_event();
    event.label = std::string(501, 'x');
    check(producer.submit(std::move(event)) == CallbackResult::internal_error,
          "unbounded callback snapshot entered the queue");
    check(producer.submit(start_event()) == CallbackResult::terminal,
          "callback accepted after boundedness failure");
    check(producer.shutdown() == ProducerResult::producer_internal_error,
          "boundedness failure was not stable");
}

void test_pause_resume_are_canonical_and_writer_sequenced() {
    auto state = std::make_shared<SinkState>();
    JournalProducer producer(options_for(state));
    check(producer.start_recording(start_request()) == ProducerResult::producer_ok, "start failed");
    check(producer.submit(start_event()) == CallbackResult::accepted, "start event rejected");
    check(producer.submit(pause_snapshot()) == CallbackResult::accepted, "pause rejected");
    check(producer.submit(resume_snapshot()) == CallbackResult::accepted, "resume rejected");
    check(producer.normal_stop(stop_request(6)) == ProducerResult::producer_ok, "stop rejected");
    check(producer.shutdown() == ProducerResult::producer_ok, "shutdown failed");
    std::lock_guard lock(state->mutex);
    check(state->lines.size() == 5, "pause/resume journal line count differs");
    check(state->lines[2].find("\"record_type\":\"pause\"") != std::string::npos &&
              state->lines[2].find("\"recording_paused\":true") != std::string::npos,
          "pause was not canonical");
    check(state->lines[3].find("\"record_type\":\"resume\"") != std::string::npos &&
              state->lines[3].find("\"recording_paused\":false") != std::string::npos,
          "resume was not canonical");
    check(state->lines[2].find("33333333-3333-4333-8333-333333333333") != std::string::npos &&
              state->lines[3].find("44444444-4444-4444-8444-444444444444") != std::string::npos,
          "pause and resume UUIDs were not distinct");
    for (std::size_t sequence = 0; sequence < state->lines.size(); ++sequence) {
        check(state->lines[sequence].find("\"sequence\":" + std::to_string(sequence)) !=
                  std::string::npos,
              "pause/resume writer sequence was not contiguous");
    }
}

void test_pause_lifecycle_rejects_invalid_order_and_accepts_paused_stop() {
    auto invalid = std::make_shared<SinkState>();
    JournalProducer bad(options_for(invalid));
    check(bad.start_recording(start_request()) == ProducerResult::producer_ok, "start failed");
    check(bad.submit(start_event()) == CallbackResult::accepted, "start event rejected");
    check(bad.submit(resume_snapshot()) == CallbackResult::accepted, "resume did not reach writer");
    check(bad.shutdown() == ProducerResult::producer_internal_error,
          "orphan resume was not fail closed");

    auto state = std::make_shared<SinkState>();
    JournalProducer producer(options_for(state));
    check(producer.start_recording(start_request()) == ProducerResult::producer_ok, "start failed");
    check(producer.submit(start_event()) == CallbackResult::accepted, "start event rejected");
    check(producer.submit(pause_snapshot()) == CallbackResult::accepted, "pause rejected");
    auto paused_stop = stop_request(5);
    paused_stop.clock = ClockSnapshot{5'000'000'000ULL, 121, true};
    check(producer.normal_stop(paused_stop) == ProducerResult::producer_ok,
          "stop while paused rejected");
    check(producer.submit(resume_snapshot(6, 121)) == CallbackResult::terminal,
          "resume after stop was accepted");
    check(producer.shutdown() == ProducerResult::producer_ok, "paused stop shutdown failed");
    std::lock_guard lock(state->mutex);
    check(state->lines.back().find("\"recording_paused\":true") != std::string::npos,
          "stop while paused did not retain pause flag");
}

}  // namespace

int main() {
    const std::vector<std::pair<const char*, std::function<void()>>> tests{
        {"normal/exact/order/repeated-shutdown", test_normal_start_snapshot_stop_shutdown_and_exact_lines},
        {"explicit-session/filename/header/single-generation",
         test_explicit_session_id_binds_filename_and_header_without_generation},
        {"explicit-session-invalid-or-mismatched",
         test_invalid_or_filename_mismatched_explicit_session_is_rejected_before_open},
        {"create-new/no-overwrite", test_existing_explicit_journal_is_not_overwritten},
        {"overflow/single-winner/terminal/drain", test_queue_full_overflow_single_winner_terminal_and_drain},
        {"stop-wins-before-overflow", test_stop_wins_before_full_observation},
        {"write-failure", test_write_failure_is_fail_closed},
        {"header-write-or-flush-failure", test_header_write_or_flush_failure_is_fail_closed},
        {"shutdown-timeout-resource-lifetime", test_shutdown_timeout_preserves_live_writer_resources},
        {"no-exception-escape", test_public_boundary_contains_factory_exception},
        {"bounded-callback-snapshot", test_unbounded_callback_snapshot_fails_without_escape},
        {"pause-resume/canonical/uuid/sequence", test_pause_resume_are_canonical_and_writer_sequenced},
        {"pause-lifecycle/invalid-order/paused-stop",
         test_pause_lifecycle_rejects_invalid_order_and_accepts_paused_stop},
    };
    int failures = 0;
    for (const auto& [name, test] : tests) {
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
