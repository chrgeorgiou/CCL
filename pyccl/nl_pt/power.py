import warnings
import numpy as np
from .. import ccllib as lib
from ..core import check
from ..pk2d import Pk2D
from ..power import linear_matter_power, nonlin_matter_power
from ..background import growth_factor
from .tracers import PTTracer

try:
    import fastpt as fpt
    HAVE_FASTPT = True
except ImportError:
    HAVE_FASTPT = False


class PTCalculator(object):
    def __init__(self, with_NC=True, with_IA=True,
                 log10k_min=-4, log10k_max=2, nk_per_decade=20,
                 pad_factor=1, low_extrap=-5, high_extrap=3,
                 P_window=None, C_window=.75):
        """ This class implements a set of methods that can be
        used to compute the various components needed to estimate
        perturbation theory correlations. These calculations are
        currently based on FAST-PT
        (https://github.com/JoeMcEwen/FAST-PT).

        Args:
            with_NC (bool): set to True if you'll want to use
                this calculator to compute correlations involving
                number counts.
            with_IA(bool): set to True if you'll want to use
                this calculator to compute correlations involving
                intrinsic alignments.
            log10k_min (float): decimal logarithm of the minimum
                Fourier scale (in Mpc^-1) for which you want to
                calculate perturbation theory quantities.
            log10k_max (float): decimal logarithm of the maximum
                Fourier scale (in Mpc^-1) for which you want to
                calculate perturbation theory quantities.
            pad_factor (float): fraction of the log(k) interval
                you want to add as padding for FFTLog calculations
                within FAST-PT.
            low_extrap (float): decimal logaritm of the minimum
                Fourier scale (in Mpc^-1) for which FAST-PT will
                extrapolate.
            high_extrap (float): decimal logaritm of the maximum
                Fourier scale (in Mpc^-1) for which FAST-PT will
                extrapolate.
            P_window (array_like or None): 2-element array describing
                the tapering window used by FAST-PT. See FAST-PT
                documentation for more details.
            C_window (float): `C_window` parameter used by FAST-PT
                to smooth the edges and avoid ringing. See FAST-PT
                documentation for more details.
        """
        assert HAVE_FASTPT, (
                "You must have the `FASTPT` python package "
                "installed to use CCL to get PT observables!")
        # TODO: JAB: I think we want to restore the option to pass
        # in a k_array and let the workspace perform the check.
        # Then we can also pass in the corresponding power spectrum,
        # if desired, in get_pt_pk2d
        # DAM: hmmm, what's the point of this? We know that FastPT
        # will only work for logarithmically spaced ks, right? In that
        # in that case we should force it on the users so that
        # nothing unexpected happens. Note that this doesn't limit
        # at all what ks you can evaluate the power spectra at,
        # since everything gets interpolated at the end.
        self.with_NC = with_NC
        self.with_IA = with_IA
        # TODO: what is this?
        # (JAB: These are fastpt settings that determine how smoothing
        # is done at the edges to avoid ringing, etc)
        # OK, we need to document this.
        self.P_window = P_window
        self.C_window = C_window

        to_do = ['one_loop_dd']
        if self.with_NC:
            to_do.append('dd_bias')
        if self.with_IA:
            to_do.append('IA')

        nk_total = int((log10k_max - log10k_min) * nk_per_decade)
        self.ks = np.logspace(log10k_min, log10k_max, nk_total)
        n_pad = pad_factor * len(self.ks)

        self.pt = fpt.FASTPT(self.ks, to_do=to_do,
                             low_extrap=low_extrap,
                             high_extrap=high_extrap,
                             n_pad=n_pad)
        self.dd_bias = None
        self.ia_ta = None
        self.ia_tt = None
        self.ia_mix = None

    def update_pk(self, pk):
        if pk.shape != self.ks.shape:
            raise ValueError("Input spectrum has wrong shape")
        if self.with_NC:
            self._get_dd_bias(pk)
        if self.with_IA:
            self._get_ia_bias(pk)

    def _get_dd_bias(self, pk):
        # Precompute quantities needed for number counts
        # power spectra.
        # TODO: should we use one_loop_dd_bias_b3nl instead of this?
        self.dd_bias = self.pt.one_loop_dd_bias(pk,
                                                P_window=self.P_window,
                                                C_window=self.C_window)

    def _get_ia_bias(self, pk):
        # Precompute quantities needed for intrinsic alignment
        # power spectra.
        self.ia_ta = self.pt.IA_ta(pk,
                                   P_window=self.P_window,
                                   C_window=self.C_window)
        self.ia_tt = self.pt.IA_tt(pk,
                                   P_window=self.P_window,
                                   C_window=self.C_window)
        self.ia_mix = self.pt.IA_mix(pk,
                                     P_window=self.P_window,
                                     C_window=self.C_window)

    def get_pgg(self, Pd1d1, g4,
                b11, b21, bs1, b12, b22, bs2,
                sub_lowk):
        """ Get the number counts auto-spectrum at the internal
        set of wavenumbers (given by this object's `ks` attribute)
        and a number of redshift values.

        Args:
            Pd1d1 (array_like): 1-loop matter power spectrum at the
                wavenumber values given by this object's `ks` list.
            g4 (array_like): fourth power of the growth factor at
                a number of redshifts.
            b11 (array_like): 1-st order bias for the first tracer
                being correlated at the same set of input redshifts.
            b21 (array_like): 2-nd order bias for the first tracer
                being correlated at the same set of input redshifts.
            bs1 (array_like): tidal bias for the first tracer
                being correlated at the same set of input redshifts.
            b12 (array_like): 1-st order bias for the second tracer
                being correlated at the same set of input redshifts.
            b22 (array_like): 2-nd order bias for the second tracer
                being correlated at the same set of input redshifts.
            bs2 (array_like): tidal bias for the second tracer
                being correlated at the same set of input redshifts.
            sub_lowk (bool): if True, the small-scale white noise
                contribution will be subtracted.

        Returns:
            array_like: 2D array of shape `(N_k, N_z)`, where `N_k` \
                is the size of this object's `ks` attribute, and \
                `N_z` is the size of the input redshift-dependent \
                biases and growth factor.
        """
        Pd1d2 = g4[None, :] * self.dd_bias[2][:, None]
        Pd2d2 = g4[None, :] * self.dd_bias[3][:, None]
        Pd1s2 = g4[None, :] * self.dd_bias[4][:, None]
        Pd2s2 = g4[None, :] * self.dd_bias[5][:, None]
        Ps2s2 = g4[None, :] * self.dd_bias[6][:, None]

        s4 = 0.
        if sub_lowk:
            s4 = g4 * self.dd_bias[7]
            s4 = s4[None, :]

        # TODO: someone else should check this
        pgg = ((b11*b12)[None, :] * Pd1d1 +
               0.5*(b11*b22 + b12*b21)[None, :] * Pd1d2 +
               0.25*(b21*b22)[None, :] * (Pd2d2 - 2.*s4) +
               0.5*(b11*bs2 + b12*bs1)[None, :] * Pd1s2 +
               0.25*(b21*bs2 + b22*bs1)[None, :] * (Pd2s2 - (4./3.)*s4) +
               0.25*(bs1*bs2)[None, :] * (Ps2s2 - (8./9.)*s4))
        return pgg

    def get_pgm(self, Pd1d1, g4, b1, b2, bs):
        """ Get the number counts - matter cross-spectrum at the
        internal set of wavenumbers (given by this object's `ks`
        attribute) and a number of redshift values.

        Args:
            Pd1d1 (array_like): 1-loop matter power spectrum at the
                wavenumber values given by this object's `ks` list.
            g4 (array_like): fourth power of the growth factor at
                a number of redshifts.
            b1 (array_like): 1-st order bias for the number counts
                tracer being correlated at the same set of input
                redshifts.
            b2 (array_like): 2-nd order bias for the number counts
                tracer being correlated at the same set of input
                redshifts.
            bs (array_like): tidal bias for the number counts
                tracer being correlated at the same set of input
                redshifts.

        Returns:
            array_like: 2D array of shape `(N_k, N_z)`, where `N_k` \
                is the size of this object's `ks` attribute, and \
                `N_z` is the size of the input redshift-dependent \
                biases and growth factor.
        """
        Pd1d2 = g4[None, :] * self.dd_bias[2][:, None]
        Pd1s2 = g4[None, :] * self.dd_bias[4][:, None]

        # TODO: someone else should check this
        pgm = (b1[None, :] * Pd1d1 +
               0.5 * b2[None, :] * Pd1d2 +
               0.5 * bs[None, :] * Pd1s2)
        return pgm

    def get_pim(self, Pd1d1, g4, c1, c2, cd):
        """ Get the intrinsic alignment - matter cross-spectrum at
        the internal set of wavenumbers (given by this object's `ks`
        attribute) and a number of redshift values.

        Args:
            Pd1d1 (array_like): 1-loop matter power spectrum at the
                wavenumber values given by this object's `ks` list.
            g4 (array_like): fourth power of the growth factor at
                a number of redshifts.
            c1 (array_like): 1-st order bias for the IA
                tracer being correlated at the same set of input
                redshifts.
            c2 (array_like): 2-nd order bias for the IA
                tracer being correlated at the same set of input
                redshifts.
            cd (array_like): overdensity bias for the IA
                tracer being correlated at the same set of input
                redshifts.

        Returns:
            array_like: 2D array of shape `(N_k, N_z)`, where `N_k` \
                is the size of this object's `ks` attribute, and \
                `N_z` is the size of the input redshift-dependent \
                biases and growth factor.
        """
        a00e, c00e, a0e0e, a0b0b = self.ia_ta
        a0e2, b0e2, d0ee2, d0bb2 = self.ia_mix

        # TODO: someone else should check this
        pim = (c1[None, :] * Pd1d1 +
               (g4*cd)[None, :] * (a00e + c00e)[:, None] +
               (g4*c2)[None, :] * (a0e2 + b0e2)[:, None])

        return pim

    def get_pgi(self, Pd1d1, g4, b1, b2, bs, c1, c2, cd):
        """ Get the number counts - IA cross-spectrum at the
        internal set of wavenumbers (given by this object's
        `ks` attribute) and a number of redshift values.

        Args:
            Pd1d1 (array_like): 1-loop matter power spectrum at the
                wavenumber values given by this object's `ks` list.
            g4 (array_like): fourth power of the growth factor at
                a number of redshifts.
            b1 (array_like): 1-st order bias for the number counts
                being correlated at the same set of input redshifts.
            b2 (array_like): 2-nd order bias for the number counts
                being correlated at the same set of input redshifts.
            bs (array_like): tidal bias for the number counts
                being correlated at the same set of input redshifts.
            c1 (array_like): 1-st order bias for the IA tracer
                being correlated at the same set of input redshifts.
            c2 (array_like): 2-nd order bias for the IA tracer
                being correlated at the same set of input redshifts.
            cd (array_like): overdensity bias for the IA tracer
                being correlated at the same set of input redshifts.

        Returns:
            array_like: 2D array of shape `(N_k, N_z)`, where `N_k` \
                is the size of this object's `ks` attribute, and \
                `N_z` is the size of the input redshift-dependent \
                biases and growth factor.
        """
        warnings.warn(
            "The full non-linear model for the cross-correlation "
            "between number counts and intrinsic alignments is "
            "still work in progress in FastPT. As a workaround "
            "CCL assumes a non-linear treatment of IAs, but only "
            "linearly biased number counts.")
        a00e, c00e, a0e0e, a0b0b = self.ia_ta
        a0e2, b0e2, d0ee2, d0bb2 = self.ia_mix

        # TODO: someone else should check this
        pim = b1[None, :] * (c1[None, :] * Pd1d1 +
                             (g4*cd)[None, :] * (a00e + c00e)[:, None] +
                             (g4*c2)[None, :] * (a0e2 + b0e2)[:, None])

        return pim

    def get_pii(self, Pd1d1, g4, c11, c21, cd1,
                c12, c22, cd2, return_bb=False):
        """ Get the intrinsic alignment auto-spectrum at the internal
        set of wavenumbers (given by this object's `ks` attribute)
        and a number of redshift values.

        Args:
            Pd1d1 (array_like): 1-loop matter power spectrum at the
                wavenumber values given by this object's `ks` list.
            g4 (array_like): fourth power of the growth factor at
                a number of redshifts.
            c11 (array_like): 1-st order bias for the first tracer
                being correlated at the same set of input redshifts.
            c21 (array_like): 2-nd order bias for the first tracer
                being correlated at the same set of input redshifts.
            cd1 (array_like): overdensity bias for the first tracer
                being correlated at the same set of input redshifts.
            c12 (array_like): 1-st order bias for the second tracer
                being correlated at the same set of input redshifts.
            c22 (array_like): 2-nd order bias for the second tracer
                being correlated at the same set of input redshifts.
            cd2 (array_like): overdensity bias for the second tracer
                being correlated at the same set of input redshifts.

        Returns:
            array_like: 2D array of shape `(N_k, N_z)`, where `N_k` \
                is the size of this object's `ks` attribute, and \
                `N_z` is the size of the input redshift-dependent \
                biases and growth factor.
        """
        a00e, c00e, a0e0e, a0b0b = self.ia_ta
        ae2e2, ab2b2 = self.ia_tt
        a0e2, b0e2, d0ee2, d0bb2 = self.ia_mix

        if return_bb:
            pii = ((cd1*cd2)[None, :] * a0b0b[:, None] +
                   (cd1*c22*g4)[None, :] * ab2b2[:, None] +
                   ((cd1*c22 + cd1*c21)*g4)[None, :] * d0bb2[:, None])
        else:
            pii = ((c11*c12*g4)[None, :] * Pd1d1 +
                   ((c11*cd2 + c12*cd1)*g4)[None, :] * (a00e + c00e)[:, None] +
                   (cd1*cd2*g4)[None, :] * a0e0e[:, None] +
                   (c21*c22*g4)[None, :] * ae2e2[:, None] +
                   ((c11*c22 + c21*c12)*g4)[None, :] * (a0e2 + b0e2)[:, None] +
                   ((cd1*c22 + cd2*c21)*g4)[None, :] * d0ee2[:, None])
        return pii


# TODO: Do we definitely want to split these into two steps?
# Is there a way to do this in a single call.
# The nice thing about the old way, other than simplicity.
# was that the PTTracers contained the info about what needed
# to be initialized.
# The nice thing about separating is that it is (probably)
# easier to store the PTworkspace to avoid re-initializing.
# DAM: we can have the best of both worlds. Make ptc an optional
# argument that defaults to None. If you don't pass it, then it
# gets initialized based on the input tracers.
def get_pt_pk2d(cosmo, tracer1, ptc=None, tracer2=None,
                sub_lowk=False, use_nonlin=True, a_arr=None,
                extrap_order_lok=1, extrap_order_hik=2,
                return_ia_bb=False):
    """Returns a :class:`~pyccl.pk2d.Pk2D` object containing
    the PT power spectrum for two quantities defined by
    two :class:`~pyccl.nl_pt.tracers.PTTracer` objects.

    Args:
        cosmo (:class:`~pyccl.core.Cosmology`): a Cosmology object.
        tracer1 (:class:`~pyccl.nl_pt.tracers.PTTracer`): the first
            tracer being correlated.
        ptc (:class:`PTCalculator`): a perturbation theory
            calculator.
        tracer2 (:class:`~pyccl.nl_pt.tracers.PTTracer`): the second
            tracer being correlated. If `None`, the auto-correlation
            of the first tracer will be returned.
        sub_lowk (bool): if True, the small-scale white noise
            contribution will be subtracted for number counts
            auto-correlations.
        use_nonlin (bool): if True, use the non-linear matter power
            spectrum as the 1-loop power spectrum. Otherwise the
            linear matter power spectrum will be used.
        a_arr (array): an array holding values of the scale factor
            at which the halo model power spectrum should be
            calculated for interpolation. If `None`, the internal
            values used by `cosmo` will be used.
        extrap_order_lok (int): extrapolation order to be used on
            k-values below the minimum of the splines. See
            :class:`~pyccl.pk2d.Pk2D`.
        extrap_order_hik (int): extrapolation order to be used on
            k-values above the maximum of the splines. See
            :class:`~pyccl.pk2d.Pk2D`.
        return_ia_bb (bool): if `True`, the B-mode power spectrum
            for intrinsic alignments will be returned (if both
            input tracers are of type
            :class:`~pyccl.nl_pt.tracers.PTIntrinsicAlignmentTracer`).

    Returns:
        :class:`~pyccl.pk2d.Pk2D`: PT power spectrum.
    """
    # TODO: Restore ability to pass in pk.
    # Could be useful for custom cases where
    # interpolated Plin isn't needed.
    # And could help with speed-up in some cases.
    # DAM: Can you describe a case when this is useful?

    if a_arr is None:
        status = 0
        na = lib.get_pk_spline_na(cosmo.cosmo)
        a_arr, status = lib.get_pk_spline_a(cosmo.cosmo, na, status)
        check(status)

    if tracer2 is None:
        tracer2 = tracer1
    if not isinstance(tracer1, PTTracer):
        raise TypeError("tracer1 must be of type `PTTracer`")
    if not isinstance(tracer2, PTTracer):
        raise TypeError("tracer2 must be of type `PTTracer`")

    if ptc is None:
        ptc = PTCalculator()
    if not isinstance(ptc, PTCalculator):
        raise TypeError("ptc should be of type `PTCalculator`")

    if (tracer1.type == 'NC') or (tracer2.type == 'NC'):
        if not ptc.with_NC:
            raise ValueError("Need number counts bias, "
                             "but workspace didn't compute it")
    if (tracer1.type == 'IA') or (tracer2.type == 'IA'):
        if not ptc.with_IA:
            raise ValueError("Need intrinsic alignment bias, "
                             "but workspace didn't compute it")

    # z
    z_arr = 1. / a_arr - 1
    # P_lin(k) at z=0
    pk_lin_z0 = linear_matter_power(cosmo, ptc.ks, 1.)
    ptc.update_pk(pk_lin_z0)

    # Linear growth factor
    ga = growth_factor(cosmo, a_arr)
    ga2 = ga**2
    ga4 = ga2**2
    # NOTE: we should add an option that does PT at every a value.
    # This isn't hard, it just takes longer.

    # Now compute the Pk using FASTPT
    # First, get P_d1d1 (the delta delta correlation), which could
    # be linear or nonlinear.
    # We will eventually want options here, e.g. to use pert theory
    # instead of halofit.
    if use_nonlin:
        Pd1d1 = np.array([nonlin_matter_power(cosmo, ptc.ks, a)
                          for a in a_arr]).T
    else:
        Pd1d1 = np.array([linear_matter_power(cosmo, ptc.ks, a)
                          for a in a_arr]).T

    if (tracer1.type == 'NC'):
        b11 = tracer1.b1(z_arr)
        b21 = tracer1.b2(z_arr)
        bs1 = tracer1.b2(z_arr)
        if (tracer2.type == 'NC'):
            b12 = tracer2.b1(z_arr)
            b22 = tracer2.b2(z_arr)
            bs2 = tracer2.b2(z_arr)

            # TODO: we're not using the 1-loop calculation at all
            #   (i.e. bias_fpt[0]).
            # Should we allow users to select that as Pd1d1?
            p_pt = ptc.get_pgg(Pd1d1, ga4,
                               b11, b21, bs1, b12, b22, bs2,
                               sub_lowk)
        elif (tracer2.type == 'IA'):
            c12 = tracer2.c1(z_arr)
            c22 = tracer2.c2(z_arr)
            cd2 = tracer2.cdelta(z_arr)
            p_pt = ptc.get_pgi(Pd1d1, ga4,
                               b11, b21, bs1, c12, c22, cd2)
        elif (tracer2.type == 'M'):
            p_pt = ptc.get_pgm(Pd1d1, ga4,
                               b11, b21, bs1)
        else:
            raise NotImplementedError("Combination %s-%s not implemented yet" %
                                      (tracer1.type, tracer2.type))
    elif (tracer1.type == 'IA'):
        c11 = tracer1.c1(z_arr)
        c21 = tracer1.c2(z_arr)
        cd1 = tracer1.cdelta(z_arr)
        if (tracer2.type == 'IA'):
            c12 = tracer2.c1(z_arr)
            c22 = tracer2.c2(z_arr)
            cd2 = tracer2.cdelta(z_arr)
            p_pt = ptc.get_pii(Pd1d1, ga4,
                               c11, c21, cd1, c12, c22, cd2,
                               return_bb=return_ia_bb)
        elif (tracer2.type == 'NC'):
            b12 = tracer2.b1(z_arr)
            b22 = tracer2.b2(z_arr)
            bs2 = tracer2.b2(z_arr)
            p_pt = ptc.get_pgi(Pd1d1, ga4,
                               b12, b22, bs2, c11, c21, cd1)
        elif (tracer2.type == 'M'):
            p_pt = ptc.get_pim(Pd1d1, ga4,
                               c11, c21, cd1)
        else:
            raise NotImplementedError("Combination %s-%s not implemented yet" %
                                      (tracer1.type, tracer2.type))
    elif (tracer1.type == 'M'):
        if (tracer2.type == 'NC'):
            b12 = tracer2.b1(z_arr)
            b22 = tracer2.b2(z_arr)
            bs2 = tracer2.b2(z_arr)
            p_pt = ptc.get_pgm(Pd1d1, ga4,
                               b12, b22, bs2)
        elif (tracer2.type == 'IA'):
            c12 = tracer2.c1(z_arr)
            c22 = tracer2.c2(z_arr)
            cd2 = tracer2.cdelta(z_arr)
            p_pt = ptc.get_pim(Pd1d1, ga4,
                               c12, c22, cd2)
        elif (tracer2.type == 'M'):
            p_pt = Pd1d1
        else:
            raise NotImplementedError("Combination %s-%s not implemented yet" %
                                      (tracer1.type, tracer2.type))
    else:
        raise NotImplementedError("Combination %s-%s not implemented yet" %
                                  (tracer1.type, tracer2.type))

    # I've initialized the tracers to None, assuming that,
    # if tracer2 is None, then the assumption is that
    # it is the same as tracer_1.

    # for matter cross correlation, we will use (for now) a
    # PTNumberCounts tracer with b1=1 and all other bias = 0

    # at some point, do we want to store an spline array for these
    # pt power?

    # nonlinear bias cross-terms with IA are currently missing from
    # here and from FASTPT come back to this.

    # Once you have created the 2-dimensional P(k) array,
    # then generate a Pk2D object as described in pk2d.py.
    pt_pk = Pk2D(a_arr=a_arr,
                 lk_arr=np.log(ptc.ks),
                 pk_arr=p_pt.T,
                 is_logp=False)

    return pt_pk
