# gr-map65

GNU Radio blocks for feeding wideband EME receivers — **MAP65**, **EME65**
(WS suite) and **QMAP** — directly over the network.

The `MAP65 UDP Sink` block takes complex I/Q from a GNU Radio flowgraph and
sends it as Linrad *timf2* UDP packets, the format these programs read when
set to network input. No virtual sound card is needed, and both polarisations
(xpol) and 192 kHz bandwidth are supported.

| Feature            | MAP65 | EME65 | QMAP (WS) |
|--------------------|:-----:|:-----:|:---------:|
| 96 kHz             | ✓     | ✓     | ✓         |
| 95.238 kHz         | ✓     | ✓     |           |
| 128 / 256 kHz      |       |       | ✓         |
| 192 kHz            |       | ✓     | ✓         |
| Xpol (X + Y)       | ✓     | ✓     |           |
| int16 / float32    | ✓     | ✓     | ✓         |

The QMAP column refers to the QMAP shipped with the WS suite; check other
QMAP builds for which rates they accept.

Note: [gr-linrad](https://github.com/daniestevez/gr-linrad) sends data *into*
Linrad in its raw16/raw24 input formats. This module sends the timf2 format
that Linrad itself sends *to* MAP65, so GNU Radio can take Linrad's place.

## Installation

Requires GNU Radio 3.10 and numpy.

```sh
git clone <repository-url> gr-map65
cd gr-map65
mkdir build && cd build
cmake ..                                   # radioconda/conda: add -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX
make
make test
make install                               # system-wide install: sudo make install && sudo ldconfig
```

The block then appears in GNU Radio Companion under **[MAP65]**. From Python:

```python
from gnuradio import map65
snk = map65.udp_sink('192.168.1.20', 50004, 144.125e6, 96000, 'int16', xpol=False)
```

## Block parameters

| Parameter          | Description |
|--------------------|-------------|
| Host, Port         | Destination of the packets. Unicast, broadcast or multicast. MAP65/EME65 listen on 50004 by default. |
| Center freq (Hz)   | Passband centre, shown as the dial frequency. Can also be set at run time on the `freq` message port, as a number or a pair such as `('fq' . 144.1e6)`. |
| Sample rate        | 96 kHz, 95.238 kHz, 192 kHz (EME65, QMAP) or 128/256 kHz (QMAP). The block does not resample; the input must already run at this rate. |
| Polarisation       | *Single*: one input. *Xpol*: inputs `x` and `y` for the two polarisations. |
| Sample format      | int16 or float32. int16 halves the network load and is recommended. |
| Scale              | Samples are multiplied by this before packing. Set it for about 30 dB Rx noise in the receiver (see below). With 32767, 1.0 is int16 full scale. |
| Flip spectrum      | Mirrors the spectrum. Only needed if the source itself delivers a mirrored spectrum. |
| Receiver           | *MAP65 / EME65*, *QMAP* or *None*. Shifts the stream so the centre frequency lands where that program expects it (see [Frequency placement](#frequency-placement)). Default MAP65 / EME65. |

## Receiver setup

1. In MAP65/EME65/QMAP: *Setup → Options*, select network input and the same UDP port.
2. Select the same sample rate. EME65 must be restarted after switching to or from 192 kHz.
3. For xpol, enable Xpol in the receiver as well. Both receive channels must share one clock.
4. Keep the receiving PC's clock NTP-synchronised. MAP65/EME65 time the data by
   their own clock, not by the packet timestamps.
5. Adjust *Scale* until the Rx noise level reads about 30 dB with no signals
   present, as the WSJT user guides recommend. That is a noise RMS of about 32
   counts per I/Q component. The right Scale depends on the SDR gain.

The stream must arrive in real time, from SDR hardware or through a Throttle block.

### Network load

| Mode                 | Packets/s (int16) | int16      | float32    |
|----------------------|------------------:|-----------:|-----------:|
| 96 kHz single        | 276       | ~3 Mbit/s  | ~6 Mbit/s  |
| 96 kHz xpol          | 552       | ~6 Mbit/s  | ~12 Mbit/s |
| 192 kHz single       | 552       | ~6 Mbit/s  | ~12 Mbit/s |
| 192 kHz xpol         | 1103      | ~12.5 Mbit/s | ~25 Mbit/s |

MAP65/EME65 use a small UDP receive buffer (about 100 packets), so prefer
int16 and a wired network at the higher rates.

## Packet format

Each 1416-byte little-endian packet is a Linrad `NET_RX_STRUCT`:

| Offset | Type    | Field              | Value |
|-------:|---------|--------------------|-------|
| 0      | float64 | passband_center    | centre frequency in MHz |
| 8      | int32   | time               | ms since 00:00 UTC of the first sample |
| 12     | float32 | userx_freq         | 0 |
| 16     | int32   | ptr                | running sample count |
| 20     | uint16  | block_no           | packet counter, wraps at 65536 |
| 22     | int8    | userx_no           | format, see below |
| 23     | int8    | passband_direction | always 1 (ignored by MAP65/EME65) |
| 24     | 1392 B  | payload            | I/Q samples |

| userx_no | Samples/packet | Sample layout        |
|---------:|---------------:|----------------------|
|  1       | 348            | I Q (int16)          |
| -1       | 174            | I Q (float32)        |
|  2       | 174            | Ix Qx Iy Qy (int16)  |
| -2       | 87             | Ix Qx Iy Qy (float32)|

The sample rate is not carried in the packets.

Linrad and MAP65/EME65 use the opposite I/Q sign convention to GNU Radio: a
tone above the centre frequency is I − jQ (see `mapsim.f90` in MAP65), and
their FFT uses the e^(+j) kernel. The block negates Q before sending, so a
normal GNU Radio stream shows the right way round.

## Frequency placement

GNU Radio puts the centre frequency at DC (0 Hz) of a complex baseband
stream. MAP65, EME65 and QMAP don't. They FFT the stream without an FFT
shift and take bin 0 to be the *lower edge* of the passband. The centre is
bin NFFT/2, i.e. fs/2 in the stream. MAP65 and EME65 also put the dial
frequency 1.27046 kHz above that, an offset inherited from Linrad's timf2
output. It is hard-coded in MAP65's `widegraph.cpp`, `plotter.cpp` and
`map65a.f90`:

```
freq = bin_freq + nkhz_center - 48.0 - 1.27046      (kHz, at 96 kHz)
```

QMAP has dropped the 1.27046 kHz from its display (`qmap/widegraph.cpp`).

So the block multiplies the stream by a phase-continuous complex exponential
that moves DC to where the receiver expects the centre:

| Receiver       | Shift              |
|----------------|--------------------|
| MAP65 / EME65  | fs/2 + 1270.46 Hz  |
| QMAP           | fs/2               |
| None           | 0 (stream as is)   |

Without the shift the whole spectrum is displaced by −49.27 kHz in
MAP65/EME65 at 96 kHz. For example, with the centre at 144.120 MHz a signal at
144.130 MHz appears at 144.0807 MHz, and a signal near the top of the
passband wraps round to the bottom. Use *None* only for other timf2 consumers
that expect the centre at DC.

The edges of the displayed span cover the stream's own band edges (±fs/2).
How much of that is usable depends on the anti-alias filter in front of the
block: a resampler with a fractional bandwidth of 0.4 leaves the outer
~10 kHz on each side dark at 96 kHz. That is the filter, not the block.

At 95.238 kHz the display centre is fs/2 = 47.619 kHz, but MAP65's decoder
assumes 48.0 kHz (`map65a.f90`). This 381 Hz discrepancy is in MAP65 itself.
The block follows the display.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

Christer Bernérus, SM6FBQ
