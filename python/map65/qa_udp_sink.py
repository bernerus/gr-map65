#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Christer Bernérus, SM6FBQ.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import socket
import struct
import time

import numpy as np
import pmt
from gnuradio import gr, gr_unittest, blocks

try:
    from gnuradio.map65 import udp_sink
except ImportError:
    import os
    import sys
    dirname, filename = os.path.split(os.path.abspath(__file__))
    sys.path.append(os.path.join(dirname, "bindings"))
    from gnuradio.map65 import udp_sink

HEADER = struct.Struct('<difiHbb')


def decode(packet):
    """Unpack a packet the way MAP65/EME65 recvpkt.f90 does: rows of I Q (Iy Qy)."""
    h = HEADER.unpack(packet[:24])
    userx_no = h[5]
    dtype = '<i2' if userx_no > 0 else '<f4'
    return h, np.frombuffer(packet[24:], dtype).astype(float).reshape(-1, 2 * abs(userx_no))


class qa_udp_sink(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
        self.rx.bind(('127.0.0.1', 0))
        self.rx.settimeout(0.5)
        self.port = self.rx.getsockname()[1]

    def tearDown(self):
        self.rx.close()
        self.tb = None

    def run_sink(self, nsamples, sample_format, xpol, scale=32767.0, **kwargs):
        rng = np.random.default_rng(1)
        chans = [(rng.uniform(-0.9, 0.9, nsamples) + 1j * rng.uniform(-0.9, 0.9, nsamples))
                 .astype(np.complex64) for _ in range(2 if xpol else 1)]
        snk = udp_sink('127.0.0.1', self.port, 144.125e6, 96000, sample_format,
                       xpol, scale, **kwargs)
        for i, c in enumerate(chans):
            self.tb.connect(blocks.vector_source_c(c.tolist()), (snk, i))
        self.tb.run()
        packets = []
        try:
            while True:
                packets.append(self.rx.recv(2000))
        except socket.timeout:
            pass
        return snk, chans, packets

    def check_format(self, sample_format, xpol, userx_no, spp):
        npackets = 20
        # A partial packet at the end must be held back, not sent
        snk, chans, packets = self.run_sink(npackets * spp + spp // 2, sample_format, xpol)
        self.assertEqual(len(packets), npackets)
        rows = []
        for i, p in enumerate(packets):
            self.assertEqual(len(p), 1416)
            h, dd = decode(p)
            self.assertAlmostEqual(h[0], 144.125)
            self.assertEqual((h[3], h[4], h[5], h[6]), (i * spp, i, userx_no, 1))
            self.assertEqual(len(dd), spp)
            rows.append(dd)
        dd = np.vstack(rows)
        expected = np.column_stack([v for c in chans for v in (c.real, c.imag)])
        expected = expected[:npackets * spp] * 32767.0
        if sample_format == 'int16':
            expected = np.rint(expected)
        np.testing.assert_allclose(dd, expected, atol=1e-2)

    def test_001_int16_single(self):
        self.check_format('int16', False, 1, 348)

    def test_002_float32_single(self):
        self.check_format('float32', False, -1, 174)

    def test_003_int16_xpol(self):
        self.check_format('int16', True, 2, 174)

    def test_004_float32_xpol(self):
        self.check_format('float32', True, -2, 87)

    def test_005_int16_clips(self):
        _, _, packets = self.run_sink(348, 'int16', False, scale=1e6)
        _, dd = decode(packets[0])
        self.assertEqual(dd.max(), 32767)
        self.assertEqual(dd.min(), -32768)

    def test_006_inverted(self):
        _, _, packets = self.run_sink(348, 'int16', False, inverted=True)
        self.assertEqual(decode(packets[0])[0][6], -1)

    def test_007_freq_message(self):
        snk = udp_sink('127.0.0.1', self.port, 144.125e6)
        self.tb.connect(blocks.vector_source_c([0j] * 348, True),
                        blocks.throttle(gr.sizeof_gr_complex, 96000), snk)
        self.tb.start()
        # Both message forms: (symbol . number) pair and plain number
        snk.to_basic_block()._post(pmt.intern('freq'),
                                   pmt.cons(pmt.intern('fq'), pmt.from_double(432.065e6)))
        time.sleep(0.2)
        snk.to_basic_block()._post(pmt.intern('freq'), pmt.from_double(1296.065e6))
        time.sleep(0.2)
        self.tb.stop()
        self.tb.wait()
        freqs = []
        try:
            while True:
                freqs.append(round(decode(self.rx.recv(2000))[0][0], 6))
        except socket.timeout:
            pass
        self.assertIn(432.065, freqs)
        self.assertEqual(freqs[-1], 1296.065)

    def test_008_timestamp(self):
        _, _, packets = self.run_sink(348, 'int16', False)
        ms = HEADER.unpack(packets[0][:24])[1]
        now = int((time.time() % 86400) * 1000)
        self.assertLess(min((now - ms) % 86400000, (ms - now) % 86400000), 5000)


if __name__ == '__main__':
    gr_unittest.run(qa_udp_sink)
