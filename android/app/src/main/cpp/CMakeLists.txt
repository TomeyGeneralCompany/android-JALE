cmake_minimum_required(VERSION 3.22.1)
project(jale_qwen LANGUAGES C CXX)

# The llama.cpp checkout is pinned and downloaded in android/ci/prepare-qwen.sh.
set(LLAMA_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../third_party/llama.cpp")
if(NOT EXISTS "${LLAMA_ROOT}/CMakeLists.txt")
    message(FATAL_ERROR "llama.cpp source absent; run android/ci/prepare-qwen.sh")
endif()

set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)
set(GGML_KLEIDIAI OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)

add_subdirectory("${LLAMA_ROOT}" "${CMAKE_BINARY_DIR}/llama-cpp")

add_library(jale_qwen SHARED jale_qwen.cpp)
target_compile_features(jale_qwen PRIVATE cxx_std_17)
target_link_libraries(jale_qwen PRIVATE llama log)
