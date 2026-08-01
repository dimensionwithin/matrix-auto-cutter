cmake_minimum_required(VERSION 3.25)

project(matrix_auto_cutter_native LANGUAGES CXX)

if(NOT WIN32)
  message(FATAL_ERROR "The native journal producer currently targets Windows/QPC only")
endif()

add_library(matrix_journal_producer STATIC
  native/src/journal_producer.cpp
)
target_include_directories(matrix_journal_producer PUBLIC native/include)
target_compile_features(matrix_journal_producer PUBLIC cxx_std_20)
target_compile_definitions(matrix_journal_producer PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(matrix_journal_producer PRIVATE bcrypt)

if(MSVC)
  target_compile_options(matrix_journal_producer PRIVATE /W4 /WX /permissive- /EHsc /utf-8)
endif()

add_executable(matrix-journal-producer native/src/standalone_main.cpp)
target_link_libraries(matrix-journal-producer PRIVATE matrix_journal_producer)
if(MSVC)
  target_compile_options(matrix-journal-producer PRIVATE /W4 /WX /permissive- /EHsc /utf-8)
endif()

include(CTest)
if(BUILD_TESTING)
  add_executable(matrix-journal-producer-tests native/tests/journal_producer_tests.cpp)
  target_link_libraries(matrix-journal-producer-tests PRIVATE matrix_journal_producer)
  if(MSVC)
    target_compile_options(matrix-journal-producer-tests PRIVATE /W4 /WX /permissive- /EHsc /utf-8)
  endif()
  add_test(NAME native-producer-core COMMAND matrix-journal-producer-tests)

  find_package(Python3 3.12 EXACT COMPONENTS Interpreter)
  if(Python3_Interpreter_FOUND)
    add_test(
      NAME native-python-journal-contract
      COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_SOURCE_DIR}/native/tests/verify_native_journal.py
        $<TARGET_FILE:matrix-journal-producer>
        ${CMAKE_SOURCE_DIR}
    )
  endif()
endif()
