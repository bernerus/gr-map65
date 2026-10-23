#
# Copyright 2008,2009 Free Software Foundation, Inc.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

# The presence of this file turns this directory into a Python package

'''
GNU Radio blocks for MAP65, EME65 and QMAP: udp_sink sends complex I/Q as
Linrad timf2 UDP packets.
'''
import os

# import pybind11 generated symbols into the map65 namespace
try:
    # this might fail if the module is python-only
    from .map65_python import *
except ModuleNotFoundError:
    pass

# import any pure python here
from .udp_sink import udp_sink
#
