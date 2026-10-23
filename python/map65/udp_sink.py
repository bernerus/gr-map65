#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Christer Bernérus, SM6FBQ.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""
MAP65 / EME65 / QMAP / Linrad compatible UDP sink.

Packs one or two (xpol) complex baseband streams into Linrad "timf2" network
packets, the format MAP65, EME65 (WS suite) and QMAP read on their UDP port
(default 50004).

Packet layout (1416 bytes, little endian, Linrad NET_RX_STRUCT):

    offset  type     field
    0       float64  passband_center    centre frequency in MHz
    8       int32    time               ms since 00:00 UTC of first sample
    12      float32  userx_freq         (unused by MAP65)
    16      int32    ptr                running sample pointer
    20      uint16   block_no           packet counter, wraps at 65536
    22      int8     userx_no           +-1 = one RF channel, +-2 = two (xpol);
                                        positive = int16, negative = float32
    23      int8     passband_direction 1 = normal, -1 = inverted
    24      ...      1392 bytes payload

    userx_no  samples/packet  sample layout
     1        348             I Q            (int16)
    -1        174             I Q            (float32)
     2        174             Ix Qx Iy Qy    (int16)
    -2         87             Ix Qx Iy Qy    (float32)

The sample rate is not carried in the packets; the receiver must be set to
the same rate. MAP65 supports 95238 and 96000 Hz, EME65 also 192000 Hz,
QMAP (WS suite, single polarisation only) 96000 to 256000 Hz.
MAP65/EME65 ignore time and block_no and time the data by the PC clock, so
the stream must arrive in real time.
"""

import socket
import struct
import time

import numpy as np
import pmt
from gnuradio import gr

HEADER = struct.Struct('<difiHbb')
PAYLOAD_BYTES = 1392
FORMAT_INT16 = 'int16'
FORMAT_FLOAT32 = 'float32'
SUPPORTED_RATES = (2e6 / 21, 96000.0, 128000.0, 192000.0, 256000.0)
# Resync the packet timestamps to the wall clock if they drift this much
MAX_DRIFT_S = 0.5


class udp_sink(gr.sync_block):
    """Send complex stream(s) as Linrad timf2 UDP packets for MAP65 / EME65.

    Parameters:
        host: Destination address (unicast, broadcast or multicast).
        port: Destination UDP port. MAP65/EME65 default is 50004.
        center_freq: Passband centre frequency in Hz, shown as the dial frequency.
        samp_rate: Input sample rate: 95238, 96000, 192000 (EME65, QMAP) or 128000/256000 (QMAP).
        sample_format: 'int16' or 'float32'.
        xpol: False = one input (userx_no +-1), True = inputs X and Y (userx_no +-2).
        scale: Samples are multiplied by this before packing. int16 samples are clipped.
        inverted: True sets passband_direction=-1 (spectrum inverted).

    Message input 'freq' takes a centre frequency in Hz, either as a plain
    number or as a pair (symbol . number), e.g. ('fq' . 144.1e6)."""

    def __init__(self, host='127.0.0.1', port=50004, center_freq=144.125e6,
                 samp_rate=96000, sample_format=FORMAT_INT16, xpol=False,
                 scale=32767.0, inverted=False):
        self.nchan = 2 if xpol else 1
        gr.sync_block.__init__(
            self,
            name='MAP65 UDP Sink',
            in_sig=[np.complex64] * self.nchan,
            out_sig=None
        )
        if sample_format not in (FORMAT_INT16, FORMAT_FLOAT32):
            raise ValueError("sample_format must be 'int16' or 'float32'")
        if min(abs(samp_rate - r) for r in SUPPORTED_RATES) > 1.0:
            gr.log.warn('map65.udp_sink: unsupported sample rate %g Hz for MAP65/EME65/QMAP'
                        % samp_rate)

        self.dest = (socket.gethostbyname(host), int(port))
        self.samp_rate = float(samp_rate)
        self.sample_format = sample_format
        sign = 1 if sample_format == FORMAT_INT16 else -1
        self.userx_no = sign * self.nchan
        # Bytes per sample: 2 (I, Q) values per channel, 2 or 4 bytes each
        self.samples_per_packet = PAYLOAD_BYTES // (2 * self.nchan * (2 if sign == 1 else 4))
        self.center_freq = center_freq
        self.scale = scale
        self.inverted = inverted

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if socket.inet_aton(self.dest[0])[0] in range(224, 240):
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)

        self.pending = np.zeros((self.nchan, 0), dtype=np.complex64)
        self.block_no = 0
        self.ptr = 0
        self.t0 = None
        self.sent_samples = 0
        self.send_failed = False

        self.message_port_register_in(pmt.intern('freq'))
        self.set_msg_handler(pmt.intern('freq'), self.handle_freq)

    def set_center_freq(self, center_freq):
        self.center_freq = float(center_freq)

    def set_scale(self, scale):
        self.scale = float(scale)

    def set_inverted(self, inverted):
        self.inverted = bool(inverted)

    def handle_freq(self, msg):
        if pmt.is_pair(msg):
            msg = pmt.cdr(msg)
        if pmt.is_number(msg):
            self.set_center_freq(pmt.to_python(msg))

    def stop(self):
        self.sock.close()
        return True

    def _timestamp_ms(self):
        now = time.time()
        if self.t0 is None:
            self.t0 = now
            self.sent_samples = 0
        t = self.t0 + self.sent_samples / self.samp_rate
        # Samples are not arriving in real time (overrun, paused source): resync
        if abs(now - t) > MAX_DRIFT_S:
            self.t0 = now
            self.sent_samples = 0
            t = now
        return int((t % 86400.0) * 1000.0)

    def _payloads(self, data):
        """Pack (nchan, npackets * spp) samples into (npackets, 1392) bytes."""
        # (nchan, n) complex -> (n, nchan * 2) floats: I Q  or  Ix Qx Iy Qy
        iq = data.T.copy().view(np.float32) * self.scale
        if self.userx_no > 0:
            iq = np.clip(np.rint(iq), -32768, 32767).astype('<i2')
        else:
            iq = iq.astype('<f4')
        return iq.reshape(-1, self.samples_per_packet * 2 * self.nchan)

    def work(self, input_items, output_items):
        n = len(input_items[0])
        data = np.concatenate((self.pending, np.vstack(input_items[:self.nchan])), axis=1)
        spp = self.samples_per_packet
        npackets = data.shape[1] // spp

        for payload in self._payloads(data[:, :npackets * spp]):
            header = HEADER.pack(self.center_freq / 1e6, self._timestamp_ms(), 0.0,
                                 self.ptr, self.block_no, self.userx_no,
                                 -1 if self.inverted else 1)
            try:
                self.sock.sendto(header + payload.tobytes(), self.dest)
                self.send_failed = False
            except OSError as e:
                if not self.send_failed:
                    gr.log.warn('map65.udp_sink: send to %s:%d failed: %s' % (self.dest + (e,)))
                    self.send_failed = True
            self.block_no = (self.block_no + 1) & 0xFFFF
            self.ptr = (self.ptr + spp) & 0x7FFFFFFF
            self.sent_samples += spp

        self.pending = data[:, npackets * spp:].copy()
        return n
