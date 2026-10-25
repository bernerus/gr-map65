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
        # Raw packing tests: no frequency shift unless asked for
        kwargs.setdefault('target', 'none')
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
        # Linrad/MAP65 convention: Q negated relative to GNU Radio
        expected = np.column_stack([v for c in chans for v in (c.real, -c.imag)])
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
        _, chans, packets = self.run_sink(348, 'int16', False, inverted=True)
        h, dd = decode(packets[0])
        self.assertEqual(h[6], 1)
        expected = np.column_stack((chans[0].real, chans[0].imag)) * 32767.0
        np.testing.assert_allclose(dd, np.rint(expected), atol=1e-2)

    def bin_freq(self, target, f, fs=96000):
        """Send a tone f Hz above centre and return the frequency, 0 to fs, that
        the receiver's FFT sees it at."""
        n = 348 * 100
        tone = (0.1 * np.exp(2j * np.pi * f * np.arange(n) / fs)).astype(np.complex64)
        snk = udp_sink('127.0.0.1', self.port, 144.125e6, fs, 'int16', False, 32767.0,
                       target=target)
        # Split the stream into many work() calls to test phase continuity
        self.tb.connect(blocks.vector_source_c(tone.tolist()),
                        blocks.stream_to_vector(gr.sizeof_gr_complex, 600),
                        blocks.vector_to_stream(gr.sizeof_gr_complex, 600), snk)
        self.tb.run()
        dd = np.vstack([decode(self.rx.recv(2000))[1] for _ in range(100)])
        z = dd[:, 0] + 1j * dd[:, 1]
        # MAP65/EME65/QMAP FFT with the e^(+j) kernel (four2a isign=+1 in
        # symspec.f90), so bin k holds a signal rotating by -2 pi k / nfft per
        # sample. They do not FFT-shift: bin k is k * fs / nfft, 0 to fs.
        step = -np.angle(z[1:] * np.conj(z[:-1]))
        # A constant step: no phase jumps between packets or work() calls
        self.assertLess(np.ptp(np.unwrap(step)), 0.01)
        return (np.mean(np.unwrap(step)) * fs / (2 * np.pi)) % fs

    @staticmethod
    def map65_display(bin_hz):
        """Offset from the centre frequency shown by MAP65/EME65 for an FFT bin:
        freq = bin_freq + nkhz - 48.0 - 1.27046 kHz (widegraph.cpp, map65a.f90)."""
        return bin_hz - 48000.0 - 1270.46

    @staticmethod
    def qmap_display(bin_hz, fs=96000):
        """Same for QMAP, which has dropped the 1.27046 kHz (qmap/widegraph.cpp)."""
        return bin_hz - fs / 2

    def test_009_tone_frequency_map65(self):
        # A tone 10 kHz above centre must show 10 kHz above centre in MAP65/EME65
        self.assertAlmostEqual(self.map65_display(self.bin_freq('map65', 10000.0)),
                               10000.0, delta=0.5)

    def test_010_tone_frequency_qmap(self):
        self.assertAlmostEqual(self.qmap_display(self.bin_freq('qmap', -10000.0)),
                               -10000.0, delta=0.5)

    def test_011_tone_frequency_unshifted(self):
        # Without the shift, MAP65/EME65 show a +10 kHz tone 39.27 kHz below
        # centre: the misplacement this shift corrects
        self.assertAlmostEqual(self.map65_display(self.bin_freq('none', 10000.0)),
                               -39270.46, delta=0.5)

    def test_012_bad_target(self):
        with self.assertRaises(ValueError):
            udp_sink('127.0.0.1', self.port, target='linrad')

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
