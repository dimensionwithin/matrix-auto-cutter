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

add_library(matrix_obs_adapter STATIC
  native/src/obs_adapter.cpp
)
target_include_directories(matrix_obs_adapter PUBLIC native/include)
target_compile_features(matrix_obs_adapter PUBLIC cxx_std_20)
target_compile_definitions(matrix_obs_adapter PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(matrix_obs_adapter PUBLIC matrix_journal_producer)
if(MSVC)
  target_compile_options(matrix_obs_adapter PRIVATE /W4 /WX /permissive- /EHsc /utf-8)
endif()

set(OBS_SDK_ROOT "" CACHE PATH "Official OBS source/header root matching the installed OBS")
option(MATRIX_BUILD_OBS_PLUGIN "Build the experimental OBS Direct-MP4 journal adapter" OFF)
if(MATRIX_BUILD_OBS_PLUGIN)
  if(NOT EXISTS "${OBS_SDK_ROOT}/libobs/obs-module.h" OR
     NOT EXISTS "${OBS_SDK_ROOT}/frontend/api/obs-frontend-api.h")
    message(FATAL_ERROR "OBS_SDK_ROOT must point to the official matching OBS source tree")
  endif()
  file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/obs-generated/config")
  file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/obs-generated/config/obsconfig.h"
    "#pragma once\n#define OBS_RELEASE_CANDIDATE 0\n#define OBS_BETA 0\n")
  add_library(matrix-auto-cutter-obs MODULE native/src/obs_plugin.cpp)
  target_compile_features(matrix-auto-cutter-obs PRIVATE cxx_std_20)
  target_compile_definitions(matrix-auto-cutter-obs PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
  target_include_directories(matrix-auto-cutter-obs PRIVATE
    native/include
    "${CMAKE_CURRENT_BINARY_DIR}/obs-generated/config"
  )
  target_include_directories(matrix-auto-cutter-obs SYSTEM PRIVATE
    "${OBS_SDK_ROOT}/libobs"
    "${OBS_SDK_ROOT}/libobs/callback"
    "${OBS_SDK_ROOT}/libobs/graphics"
    "${OBS_SDK_ROOT}/libobs/media-io"
    "${OBS_SDK_ROOT}/libobs/util"
    "${OBS_SDK_ROOT}/frontend/api"
  )
  target_link_libraries(matrix-auto-cutter-obs PRIVATE matrix_obs_adapter)
  set_target_properties(matrix-auto-cutter-obs PROPERTIES PREFIX "")
  if(MSVC)
    target_compile_options(matrix-auto-cutter-obs PRIVATE /W4 /WX /permissive- /EHsc /utf-8)
  endif()
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

  add_executable(matrix-obs-adapter-tests native/tests/obs_adapter_tests.cpp)
  target_link_libraries(matrix-obs-adapter-tests PRIVATE matrix_obs_adapter)
  if(MSVC)
    target_compile_options(matrix-obs-adapter-tests PRIVATE /W4 /WX /permissive- /EHsc /utf-8)
  endif()
  add_test(NAME native-obs-adapter COMMAND matrix-obs-adapter-tests)

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
