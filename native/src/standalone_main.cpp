#include "matrix_auto_cutter/journal_producer.hpp"

#include <Windows.h>

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace {

using matrix_auto_cutter::CallbackResult;
using matrix_auto_cutter::CalibrationSnapshot;
using matrix_auto_cutter::ClockSnapshot;
using matrix_auto_cutter::EventSnapshot;
using matrix_auto_cutter::EventType;
using matrix_auto_cutter::JournalProducer;
using matrix_auto_cutter::ProducerOptions;
using matrix_auto_cutter::ProducerResult;
using matrix_auto_cutter::PauseSnapshot;
using matrix_auto_cutter::RecordingStart;
using matrix_auto_cutter::RecordingStop;
using matrix_auto_cutter::ResumeSnapshot;

struct Arguments final {
    std::filesystem::path journal;
    std::string recording_utf8;
    std::uint64_t duration_ns{};
    std::uint64_t final_frame_count{};
    std::optional<std::uint64_t> pause_start_ns;
    std::optional<std::uint64_t> resume_ns;
    std::size_t queue_capacity{matrix_auto_cutter::default_queue_capacity};
};

std::optional<std::string> utf8(const std::wstring_view value) {
    if (value.empty()) {
        return std::string{};
    }
    const int size = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        value.data(),
        static_cast<int>(value.size()),
        nullptr,
        0,
        nullptr,
        nullptr);
    if (size <= 0) {
        return std::nullopt;
    }
    std::string result(static_cast<std::size_t>(size), '\0');
    if (WideCharToMultiByte(
            CP_UTF8,
            WC_ERR_INVALID_CHARS,
            value.data(),
            static_cast<int>(value.size()),
            result.data(),
            size,
            nullptr,
            nullptr) != size) {
        return std::nullopt;
    }
    return result;
}

template <typename Integer>
bool parse_integer(const std::wstring_view value, Integer& output) {
    const auto narrow = utf8(value);
    if (!narrow.has_value() || narrow->empty() || narrow->front() == '-') {
        return false;
    }
    const auto [end, error] =
        std::from_chars(narrow->data(), narrow->data() + narrow->size(), output);
    return error == std::errc{} && end == narrow->data() + narrow->size();
}

void usage() {
    std::cerr
        << "Usage: matrix-journal-producer --journal <path> --recording <mp4-path> "
           "--duration-ns <nanoseconds> --final-frame-count <frames> "
           "[--pause-start-ns <nanoseconds> --resume-ns <nanoseconds>] "
           "[--queue-capacity <count>]\n";
}

std::optional<Arguments> parse_arguments(const int argc, wchar_t** argv) {
    Arguments result;
    bool journal = false;
    bool recording = false;
    bool duration = false;
    bool frames = false;
    for (int index = 1; index < argc; ++index) {
        const std::wstring_view key(argv[index]);
        if (index + 1 >= argc) {
            return std::nullopt;
        }
        const std::wstring_view value(argv[++index]);
        if (key == L"--journal") {
            result.journal = std::filesystem::path(value);
            journal = !result.journal.empty();
        } else if (key == L"--recording") {
            const auto converted = utf8(value);
            if (!converted.has_value()) {
                return std::nullopt;
            }
            result.recording_utf8 = *converted;
            recording = !result.recording_utf8.empty();
        } else if (key == L"--duration-ns") {
            duration = parse_integer(value, result.duration_ns) && result.duration_ns > 0;
        } else if (key == L"--final-frame-count") {
            frames = parse_integer(value, result.final_frame_count) &&
                     result.final_frame_count > 0;
        } else if (key == L"--pause-start-ns") {
            std::uint64_t parsed = 0;
            if (!parse_integer(value, parsed)) {
                return std::nullopt;
            }
            result.pause_start_ns = parsed;
        } else if (key == L"--resume-ns") {
            std::uint64_t parsed = 0;
            if (!parse_integer(value, parsed)) {
                return std::nullopt;
            }
            result.resume_ns = parsed;
        } else if (key == L"--queue-capacity") {
            if (!parse_integer(value, result.queue_capacity) || result.queue_capacity == 0) {
                return std::nullopt;
            }
        } else {
            return std::nullopt;
        }
    }
    if (!journal || !recording || !duration || !frames) {
        return std::nullopt;
    }
    if (result.pause_start_ns.has_value() != result.resume_ns.has_value() ||
        (result.pause_start_ns.has_value() &&
         (*result.pause_start_ns == 0 || *result.pause_start_ns >= *result.resume_ns ||
          *result.resume_ns >= result.duration_ns))) {
        return std::nullopt;
    }
    const auto active_ns = result.duration_ns -
                           (result.pause_start_ns.has_value()
                                ? *result.resume_ns - *result.pause_start_ns
                                : 0);
    const long double expected = static_cast<long double>(result.final_frame_count) *
                                 1'000'000'000.0L / 60.0L;
    const long double difference =
        expected > static_cast<long double>(active_ns) ? expected - static_cast<long double>(active_ns)
                                                        : static_cast<long double>(active_ns) - expected;
    if (difference / expected > 0.0005L) {
        std::cerr << "duration and final frame count exceed the 500 ppm clock gate\n";
        return std::nullopt;
    }
    return result;
}

bool accepted(const CallbackResult result, const char* operation) {
    if (result == CallbackResult::accepted) {
        return true;
    }
    std::cerr << operation << " failed: " << matrix_auto_cutter::to_string(result) << '\n';
    return false;
}

}  // namespace

int wmain(const int argc, wchar_t** argv) {
    const auto arguments = parse_arguments(argc, argv);
    if (!arguments.has_value()) {
        usage();
        return 2;
    }

    ProducerOptions options;
    options.queue_capacity = arguments->queue_capacity;
    JournalProducer producer(std::move(options));
    const ProducerResult started = producer.start_recording(RecordingStart{
        arguments->journal,
        arguments->recording_utf8,
        "0.2.0-native-standalone",
        "standalone-no-obs",
    });
    if (started != ProducerResult::producer_ok) {
        std::cerr << "recording start failed: " << matrix_auto_cutter::to_string(started) << '\n';
        return 1;
    }

    if (!accepted(
            producer.submit(EventSnapshot{
                matrix_auto_cutter::uuid_v4(),
                EventType::recording_started,
                ClockSnapshot{0, 0, false},
                std::nullopt,
                std::nullopt,
                std::nullopt,
            }),
            "recording_started")) {
        static_cast<void>(producer.shutdown());
        return 1;
    }

    const auto active_elapsed = [&](const std::uint64_t wall_ns) {
        if (!arguments->pause_start_ns.has_value() || wall_ns <= *arguments->pause_start_ns) {
            return wall_ns;
        }
        const auto paused = wall_ns < *arguments->resume_ns
                                ? wall_ns - *arguments->pause_start_ns
                                : *arguments->resume_ns - *arguments->pause_start_ns;
        return wall_ns - paused;
    };
    const auto counter_at = [&](const std::uint64_t wall_ns) {
        return static_cast<std::uint64_t>(std::llround(
            static_cast<long double>(arguments->final_frame_count) *
            static_cast<long double>(active_elapsed(wall_ns)) /
            static_cast<long double>(active_elapsed(arguments->duration_ns))));
    };
    constexpr std::uint64_t sample_period_ns = 2'000'000'000ULL;
    std::vector<std::uint64_t> points;
    for (std::uint64_t sample_ns = sample_period_ns; sample_ns < arguments->duration_ns;
         sample_ns += sample_period_ns) {
        points.push_back(sample_ns);
    }
    if (arguments->pause_start_ns.has_value()) {
        points.push_back(*arguments->pause_start_ns);
        points.push_back(*arguments->resume_ns);
    }
    std::sort(points.begin(), points.end());
    points.erase(std::unique(points.begin(), points.end()), points.end());
    for (const auto point : points) {
        if (arguments->pause_start_ns.has_value() && point == *arguments->pause_start_ns &&
            !accepted(producer.submit(PauseSnapshot{
                          matrix_auto_cutter::uuid_v4(),
                          ClockSnapshot{point, counter_at(point), true}}),
                      "pause")) {
            static_cast<void>(producer.shutdown());
            return 1;
        }
        if (arguments->resume_ns.has_value() && point == *arguments->resume_ns &&
            !accepted(producer.submit(ResumeSnapshot{
                          matrix_auto_cutter::uuid_v4(),
                          ClockSnapshot{point, counter_at(point), false}}),
                      "resume")) {
            static_cast<void>(producer.shutdown());
            return 1;
        }
        if ((arguments->pause_start_ns.has_value() && point >= *arguments->pause_start_ns &&
             point <= *arguments->resume_ns) || point % sample_period_ns != 0) {
            continue;
        }
        if (!accepted(
                producer.submit(CalibrationSnapshot{ClockSnapshot{point, counter_at(point), false}}),
                "calibration_sample")) {
            static_cast<void>(producer.shutdown());
            return 1;
        }
    }

    const ProducerResult stopped = producer.normal_stop(RecordingStop{
        ClockSnapshot{arguments->duration_ns, arguments->final_frame_count, false},
        arguments->recording_utf8,
    });
    if (stopped != ProducerResult::producer_ok) {
        std::cerr << "recording stop failed: " << matrix_auto_cutter::to_string(stopped) << '\n';
        static_cast<void>(producer.shutdown());
        return 1;
    }
    const ProducerResult closed = producer.shutdown();
    if (closed != ProducerResult::producer_ok) {
        std::cerr << "producer shutdown failed: " << matrix_auto_cutter::to_string(closed) << '\n';
        return 1;
    }
    std::cout << "native journal producer completed\n"
              << "recording_session_id=" << producer.recording_session_id() << '\n'
              << "producer_version=0.2.0-native-standalone\n";
    return 0;
}
