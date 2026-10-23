# Copyright 2020 Free Software Foundation, Inc.
#
# This file is part of GNU Radio
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

########################################################################
# Check if there is C++ code at all
########################################################################
if(NOT map65_sources)
    message(STATUS "No C++ sources... skipping python bindings")
    return()
endif(NOT map65_sources)

########################################################################
# Check for pygccxml
########################################################################
gr_python_check_module_raw("pygccxml" "import pygccxml" PYGCCXML_FOUND)

include(GrPybind)

########################################################################
# Python Bindings
########################################################################

list(APPEND map65_python_files python_bindings.cc)

gr_pybind_make_oot(map65 ../../.. gr::map65 "${map65_python_files}")

# copy bindings extension for use in QA test module
add_custom_command(
    TARGET map65_python
    POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:map65_python>
            ${PROJECT_BINARY_DIR}/test_modules/gnuradio/map65/)

install(
    TARGETS map65_python
    DESTINATION ${GR_PYTHON_DIR}/gnuradio/map65
    COMPONENT pythonapi)
