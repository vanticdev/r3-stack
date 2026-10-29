"""Analytic 2.4 GHz propagation: the **labeled fallback** for environments without Sionna RT,
and the closed-form two-ray reference used to validate the ray tracer.

Two-ray ground reflection, full form with a complex Fresnel reflection
coefficient of a lossy dielectric ground (relative permittivity eps_r,
conductivity sigma):  eps = eps_r - j 60 sigma lambda,
Gamma_V = (eps sin(psi) - sqrt(eps - cos^2 psi)) / (eps sin(psi) + sqrt(eps - cos^2 psi)),
Gamma_H = (sin(psi) - sqrt(eps - cos^2 psi)) / (sin(psi) + sqrt(eps - cos^2 psi)),
times a Rayleigh roughness factor exp(-8 (pi sigma_h sin(psi) / lambda)^2).
Field sum E = (lambda / 4 pi) [g_los exp(-j k d1) / d1 + Gamma g_ref exp(-j k d2) / d2],
PL = -20 log10 |E| (g = product of the TX and RX field patterns along each ray;
isotropic in the fallback). Beyond the breakpoint d_c = 4 h_t h_r / lambda the
loss approaches 40 log10(d) - 20 log10(h_t h_r).

Obstacles (fallback only): single knife-edge diffraction per static box
(ITU-R P.526): nu = h sqrt(2 (d1 + d2) / (lambda d1 d2)),
J(nu) = 6.9 + 20 log10(sqrt((nu - 0.1)^2 + 1) + nu - 0.1) for nu > -0.78.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.units import to_si

SPEED_OF_LIGHT = 299_792_458.0
#: Peak gain of a half-wave dipole, 10 log10(1.641) dBi.
HALF_WAVE_DIPOLE_GAIN_DBI = 2.15


def reflection_coefficient(grazing: float, permittivity: complex, polarization: str = "vertical") -> complex:
    sin_psi, cos_psi = math.sin(grazing), math.cos(grazing)
    root = cmath.sqrt(permittivity - cos_psi * cos_psi)
    if polarization in ("horizontal", "H"):
        return (sin_psi - root) / (sin_psi + root)
    return (permittivity * sin_psi - root) / (permittivity * sin_psi + root)


def knife_edge_loss_db(nu: float) -> float:
    if nu <= -0.78:
        return 0.0
    return 6.9 + 20.0 * math.log10(math.sqrt((nu - 0.1) ** 2 + 1.0) + nu - 0.1)


def half_wave_dipole_field(theta: float) -> float:
    """Field pattern of a vertical half-wave dipole vs zenith angle (amplitude, peak sqrt(1.641))."""
    s = math.sin(theta)
    if abs(s) < 1e-9:
        return 0.0
    return math.sqrt(1.641) * math.cos(math.pi / 2.0 * math.cos(theta)) / s


def itu_ground_permittivity(name: str, frequency: float) -> complex:
    """ITU-R P.2040 Table 3: eps_r = a f^b, sigma = c f^d (f in GHz); eps = eps_r - j sigma / (2 pi f eps0)."""
    table = {"very_dry_ground": (3.0, 0.0, 0.00015, 2.52), "medium_dry_ground": (15.0, -0.1, 0.035, 1.63),
             "wet_ground": (30.0, -0.4, 0.15, 1.30), "concrete": (5.24, 0.0, 0.0462, 0.7822), "metal": (1.0, 0.0, 1e7, 0.0)}
    a, b, c, d = table[name]
    f_ghz = frequency / 1e9
    eps_r, sigma = a * f_ghz ** b, c * f_ghz ** d
    return complex(eps_r, -60.0 * sigma * SPEED_OF_LIGHT / frequency)


def two_ray_dipole_gain_db(tx: np.ndarray, rx: np.ndarray, frequency: float, permittivity: complex,
                           polarization: str = "V") -> float:
    """Closed-form two-ray channel gain between two vertical half-wave dipoles over a flat
    lossy ground (smooth surface). Reference for validating the ray tracer."""
    lam = SPEED_OF_LIGHT / frequency
    k = 2.0 * math.pi / lam
    horizontal = max(math.hypot(rx[0] - tx[0], rx[1] - tx[1]), 1e-6)
    h_t, h_r = float(tx[2]), float(rx[2])
    d_los = math.hypot(horizontal, h_t - h_r)
    d_ref = math.hypot(horizontal, h_t + h_r)
    th_los = math.atan2(horizontal, h_r - h_t)  # zenith angle of the LoS ray at the RX, pointing back to the TX
    th_ref = math.atan2(horizontal, -(h_t + h_r))  # reflected ray arrives from below the horizon at the RX
    g_los = half_wave_dipole_field(math.pi - th_los) * half_wave_dipole_field(th_los)
    g_ref = half_wave_dipole_field(th_ref) * half_wave_dipole_field(th_ref)
    gamma = reflection_coefficient(math.atan2(h_t + h_r, horizontal), permittivity, polarization)
    field_sum = lam / (4.0 * math.pi) * (g_los * cmath.exp(-1j * k * d_los) / d_los + gamma * g_ref * cmath.exp(-1j * k * d_ref) / d_ref)
    return 20.0 * math.log10(max(abs(field_sum), 1e-30))


@dataclass
class Propagation:
    frequency: float
    permittivity: float = 15.0
    conductivity: float = 0.01  # S/m, moist ground at 2.4 GHz (estimate)
    roughness: float = 0.01  # m RMS (mown grass, estimate)
    polarization: str = "vertical"
    obstacles: list[dict[str, Any]] = field(default_factory=list)

    @property
    def wavelength(self) -> float:
        return SPEED_OF_LIGHT / self.frequency

    def complex_permittivity(self) -> complex:
        return complex(self.permittivity, -60.0 * self.conductivity * self.wavelength)

    def breakpoint_distance(self, h_tx: float, h_rx: float) -> float:
        return 4.0 * h_tx * h_rx / self.wavelength

    def two_ray_loss_db(self, tx: np.ndarray, rx: np.ndarray) -> float:
        lam = self.wavelength
        k = 2.0 * math.pi / lam
        dx, dy = rx[0] - tx[0], rx[1] - tx[1]
        horizontal = max(math.hypot(dx, dy), 1e-3)
        h_t, h_r = max(tx[2], 1e-3), max(rx[2], 1e-3)
        d_los = math.sqrt(horizontal ** 2 + (h_t - h_r) ** 2)
        d_ref = math.sqrt(horizontal ** 2 + (h_t + h_r) ** 2)
        grazing = math.atan2(h_t + h_r, horizontal)
        gamma = reflection_coefficient(grazing, self.complex_permittivity(), self.polarization)
        gamma *= math.exp(-8.0 * (math.pi * self.roughness * math.sin(grazing) / lam) ** 2)
        field_sum = cmath.exp(-1j * k * d_los) / d_los + gamma * cmath.exp(-1j * k * d_ref) / d_ref
        gain = (lam / (4.0 * math.pi)) ** 2 * abs(field_sum) ** 2
        return -10.0 * math.log10(max(gain, 1e-30))

    def obstacle_loss_db(self, tx: np.ndarray, rx: np.ndarray) -> float:
        """Knife-edge loss of the worst obstacle (boxes: center, size)."""
        lam = self.wavelength
        worst = 0.0
        path = rx - tx
        horizontal = math.hypot(path[0], path[1])
        if horizontal < 1e-6:
            return 0.0
        u = np.array([path[0] / horizontal, path[1] / horizontal])
        for obstacle in self.obstacles:
            center = np.asarray([to_si(v) for v in obstacle.get("center", [0, 0, 0])], dtype=float)
            size = np.asarray([to_si(v) for v in obstacle.get("size", [0.3, 0.3, 0.3])], dtype=float)
            rel = center[:2] - tx[:2]
            s = float(rel @ u)
            if s <= 0.0 or s >= horizontal:
                continue
            lateral = abs(float(rel[0] * u[1] - rel[1] * u[0]))
            half_extent = 0.5 * float(np.hypot(size[0] * u[1], size[1] * u[0]))  # footprint half-width across the path
            top = center[2] + size[2] / 2.0
            z_los = tx[2] + (rx[2] - tx[2]) * s / horizontal
            if lateral <= half_extent:
                h = top - z_los
            else:
                h = -math.hypot(lateral - half_extent, max(0.0, z_los - top))
            d1, d2 = s, horizontal - s
            nu = h * math.sqrt(2.0 * (d1 + d2) / (lam * d1 * d2))
            worst = max(worst, knife_edge_loss_db(nu))
        return worst


@dataclass
class ChannelSample:
    """Channel between the pilot handset and the car antenna, antenna gains included (dB)."""

    gain_db: float  # coherent (narrowband, at the hop frequency): P_rx = P_tx + gain_db
    mean_gain_db: float  # local mean: incoherent power sum over paths (no small-scale fading)
    obstacle_db: float = 0.0  # separate knife-edge term (analytic fallback only; RT includes it in gain_db)
    paths: int = 0
    los: bool = True


class AnalyticPropagation:
    """FALLBACK propagation: closed-form two-ray + knife edges, isotropic antennas with catalog peak gains.
    Used only when Sionna RT is unavailable (or explicitly forced); always reported by a diagnostic."""

    name = "analytic_two_ray"
    label = "analytic two-ray fallback"

    def __init__(self, tx_position: np.ndarray, propagation: Propagation, tx_gain_dbi: float, rx_gain_dbi: float) -> None:
        self.tx_position = np.asarray(tx_position, dtype=float)
        self.propagation = propagation
        self.antenna_gain_db = tx_gain_dbi + rx_gain_dbi

    def channel(self, position: np.ndarray, rotation: np.ndarray | None = None, frequency: float | None = None) -> ChannelSample:
        loss = self.propagation.two_ray_loss_db(self.tx_position, position)
        obstacle = self.propagation.obstacle_loss_db(self.tx_position, position)
        gain = self.antenna_gain_db - loss
        return ChannelSample(gain, gain, obstacle, 2, obstacle == 0.0)

    def static_gain_db(self, points: np.ndarray, frequency: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        gains = np.array([self.antenna_gain_db - self.propagation.two_ray_loss_db(self.tx_position, p)
                          - self.propagation.obstacle_loss_db(self.tx_position, p) for p in np.atleast_2d(points)])
        return gains, gains

    def stats(self) -> dict[str, Any]:
        return {"backend": self.name}

    def assumptions(self) -> list[str]:
        p = self.propagation
        return [f"FALLBACK analytic propagation (Sionna RT unavailable): two-ray ground reflection with complex Fresnel "
                f"coefficient (eps_r {p.permittivity}, sigma {p.conductivity} S/m, roughness {p.roughness} m: estimates), "
                "isotropic elevation pattern, static obstacles as single knife edges, no car body, no multipath beyond the two rays."]
