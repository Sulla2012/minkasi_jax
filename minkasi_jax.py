import minkasi
import minkasi_nb

import timeit

import numpy as np

import jax
import jax.numpy as jnp
JAX_DEBUG_NANS = True
from jax.config import config
import jax.scipy.optimize as sopt

from functools import partial

config.update("jax_debug_nans", True)
config.update("jax_enable_x64", True)
#Todo: find some way to make this dynamic so you're not stuck using cpu if you have a gpu
jax.config.update('jax_platform_name','cpu')

from astropy.cosmology import Planck15 as cosmo
from astropy import constants as const
from astropy import units as u



def eliptical_gauss(p, x, y):
    #Gives the value of an eliptical gaussian with its center at x0, y0, evaluated at x,y, where theta1 and theta2 are the
    #FWHM of the two axes and psi is the roation angle. Amp is the amplitude
    #Note x,y are at the end to make the gradient indicies make more sense i.e. grad(numarg = 0) is grad wrt x0

    x0,y0,theta1,theta2,psi,amp = p

    theta1_inv=1/theta1
    theta2_inv=1/theta2
    theta1_inv_sqr=theta1_inv**2
    theta2_inv_sqr=theta2_inv**2
    cosdec=jnp.cos(y0)
    sindec=jnp.sin(y0)/jnp.cos(y0)
    cospsi=jnp.cos(psi)
    cc=cospsi**2
    sinpsi=jnp.sin(psi)
    ss=sinpsi**2
    cs=cospsi*sinpsi

    delx=(x-x0)*cosdec
    dely=y-y0
    xx=delx*cospsi+dely*sinpsi
    yy=dely*cospsi-delx*sinpsi
    xfac=theta1_inv_sqr*xx*xx
    yfac=theta2_inv_sqr*yy*yy
    rr=xfac+yfac
    rrpow=jnp.exp(-0.5*rr)

    return amp*rrpow

@jax.jit
def gauss(p, x, y):
    #Gives the value of an eliptical gaussian with its center at x0, y0, evaluated at x,y, where theta1 and theta2 are the
    #FWHM of the two axes and psi is the roation angle. Amp is the amplitude
    #Note x,y are at the end to make the gradient indicies make more sense i.e. grad(numarg = 0) is grad wrt x0

    x0,y0,sigma,amp = p

    sigma_inv=1/sigma
    sigma_inv_sqr=sigma_inv**2
    cosdec=jnp.cos(y0)
    sindec=jnp.sin(y0)/jnp.cos(y0)
    cospsi = 1
    cc=1
    sinpsi=0
    ss=sinpsi**2
    cs=cospsi*sinpsi

    delx=(x-x0)*cosdec
    dely=y-y0
    xx=delx*cospsi+dely*sinpsi
    yy=dely*cospsi-delx*sinpsi
    xfac=sigma_inv_sqr*xx*xx
    yfac=sigma_inv_sqr*yy*yy
    rr=xfac+yfac
    rrpow=jnp.exp(-0.5*rr)

    return amp*rrpow



# Constants
# --------------------------------------------------------

h70 = cosmo.H0.value/7.00E+01

Tcmb = 2.7255
kb = const.k_B.value
me = ((const.m_e*const.c**2).to(u.keV)).value
h  = const.h.value
Xthom = const.sigma_T.to(u.cm**2).value

Mparsec = u.Mpc.to(u.cm)

# Cosmology
# --------------------------------------------------------
#Generate vectors of some cosmological quatities that we'll interpolate
#so as not to make calls to the astropy functions later
dzline = np.linspace(0.00,5.00,1000)
daline = cosmo.angular_diameter_distance(dzline)/u.radian
nzline = cosmo.critical_density(dzline)
hzline = cosmo.H(dzline)/cosmo.H0

daline = daline.to(u.Mpc/u.arcsec)
nzline = nzline.to(u.Msun/u.Mpc**3)

dzline = jnp.array(dzline)
hzline = jnp.array(hzline.value)
nzline = jnp.array(nzline.value)
daline = jnp.array(daline.value)

# Filtering
# -------------------------------------------------------
@partial(jax.jit, static_argnums = (1,))
def tod_hi_pass(tod, N_filt):
    #high pass filter a tod, with a tophat at N_filt
    mask = jnp.ones(tod.shape)
    mask = jax.ops.index_update(mask, jax.ops.index[...,:N_filt], 0.)
    
    ## apply the filter in fourier space
    Ftod = jnp.fft.fft(tod)
    Ftod_filtered = Ftod * mask
    tod_filtered = jnp.fft.ifft(Ftod_filtered).real
    return tod_filtered

#ToD poly sub

@partial(jax.jit, static_argnums = (1,2,3))
def poly(x, c0, c1, c2):
    return c0+c1*x+c2*x**2



# Compton y to Kcmb
# --------------------------------------------------------
@partial(jax.jit, static_argnums = (0,1))
def y2K_CMB(freq,Te):
  #converts compton y to delta K_CMB. Includes relativistic corrections
  #Inputs: observation frequency, in Hz, electron temperature 
  #note that both freq and Te are static arguments for the jit compiler:
  #changing these arguments between calls of this function will cause it
  # to recompile, incuring significant overhead. 
  #Outputs: delta T_cmb corresponding to the imput y, given the frequncy 
  #of observation and Te
  x = freq*h/kb/Tcmb
  xt = x/jnp.tanh(0.5*x)
  st = x/jnp.sinh(0.5*x)
  Y0 = -4.0+xt
  Y1 = -10.+((47./2.)+(-(42./5.)+(7./10.)*xt)*xt)*xt+st*st*(-(21./5.)+(7./5.)*xt)
  Y2 = (-15./2.)+((1023./8.)+((-868./5.)+((329./5.)+((-44./5.)+(11./30.)*xt)*xt)*xt)*xt)*xt+ \
       ((-434./5.)+((658./5.)+((-242./5.)+(143./30.)*xt)*xt)*xt+(-(44./5.)+(187./60.)*xt)*(st*st))*st*st
  Y3 = (15./2.)+((2505./8.)+((-7098./5.)+((14253./10.)+((-18594./35.)+((12059./140.)+((-128./21.)+(16./105.)*xt)*xt)*xt)*xt)*xt)*xt)*xt+ \
       (((-7098./10.)+((14253./5.)+((-102267./35.)+((156767./140.)+((-1216./7.)+(64./7.)*xt)*xt)*xt)*xt)*xt) +
       (((-18594./35.)+((205003./280.)+((-1920./7.)+(1024./35.)*xt)*xt)*xt) +((-544./21.)+(992./105.)*xt)*st*st)*st*st)*st*st
  Y4 = (-135./32.)+((30375./128.)+((-62391./10.)+((614727./40.)+((-124389./10.)+((355703./80.)+((-16568./21.)+((7516./105.)+((-22./7.)+(11./210.)*xt)*xt)*xt)*xt)*xt)*xt)*xt)*xt)*xt + \
       ((-62391./20.)+((614727./20.)+((-1368279./20.)+((4624139./80.)+((-157396./7.)+((30064./7.)+((-2717./7.)+(2761./210.)*xt)*xt)*xt)*xt)*xt)*xt)*xt + \
       ((-124389./10.)+((6046951./160.)+((-248520./7.)+((481024./35.)+((-15972./7.)+(18689./140.)*xt)*xt)*xt)*xt)*xt +\
       ((-70414./21.)+((465992./105.)+((-11792./7.)+(19778./105.)*xt)*xt)*xt+((-682./7.)+(7601./210.)*xt)*st*st)*st*st)*st*st)*st*st
  factor = Y0+(Te/me)*(Y1+(Te/me)*(Y2+(Te/me)*(Y3+(Te/me)*Y4)))
  return factor*Tcmb

@partial(jax.jit,static_argnums=(0,))
def K_CMB2K_RJ(freq):
  x = freq*h/kb/Tcmb
  return jnp.exp(x)*x*x/jnp.expm1(x)**2

@partial(jax.jit, static_argnums = (0,1))
def y2K_RJ(freq,Te):
  factor = y2K_CMB(freq,Te)
  return factor*K_CMB2K_RJ(freq)

#Jax atmospheric fitter
@jax.jit
def poly_sub(x, y):
    #Function which fits a 2nd degree polynomial to TOD data and then returns the best fit parameters
    #Inputs: designed to work with x = tod.info['apix'][j], y = tod.info['dat_calib'][j] - tod.info['cm']
    #which fits apix vs data-common mode, but in principle you can fit whatever
    #Outputs: res.x, the best fit parameters in the order c0, c1, c2 for y = c0 + c1*x + c2*x**2
    #TODO: this can be improved to accept arbitrary functions to fit, but I think for now
    #leaving it is fine as I suspect that would incur a performance penalty 

    #compute the residual function for a 2nd degree poly, using res**2
    poly_resid = lambda p, x, y: jnp.sum(((p[0] + p[1]*x + p[2]*x**2)-y)**2)
    p0 = jnp.array([0.1, 0.1, 0.1])
    #minimize the residual
    res = sopt.minimize(poly_resid, p0, args = (x, y), method = 'BFGS')
        
    return res.x

# Beam-convolved gNFW profiel
# --------------------------------------------------------
def conv_int_gnfw(p,xi,yi,z,max_R=10.00,fwhm=9.,freq=90e9,T_electron=5.0,r_map=15.0*60,dr=0.5):
  x0, y0, P0, c500, alpha, beta, gamma, m500 = p

  hz = jnp.interp(z,dzline,hzline)
  nz = jnp.interp(z,dzline,nzline)

  ap = 0.12

  r500 = (m500/(4.00*jnp.pi/3.00)/5.00E+02/nz)**(1.00/3.00)
  P500 = 1.65E-03*(m500/(3.00E+14/h70))**(2.00/3.00+ap)*hz**(8.00/3.00)*h70**2

  dR = max_R/2e3
  r = jnp.arange(0.00,max_R,dR)+dR/2.00

  x = r/r500
  pressure = P500*P0/((c500*x)**gamma*(1.00+(c500*x)**alpha)**((beta-gamma)/alpha))

  rmap = jnp.arange(1e-10,r_map,dr)
  r_in_Mpc = rmap*(jnp.interp(z,dzline,daline))
  rr = jnp.meshgrid(r_in_Mpc,r_in_Mpc)
  rr = jnp.sqrt(rr[0]**2+rr[1]**2)
  yy = jnp.interp(rr,r,pressure,right=0.)

  XMpc = Xthom*Mparsec

  ip = jnp.sum(yy,axis=1)*2.*XMpc/(me*1000)

  x = jnp.arange(-1.5*fwhm//(dr),1.5*fwhm//(dr))*(dr)
  beam = jnp.exp(-4*np.log(2)*x**2/fwhm**2)
  beam = beam/jnp.sum(beam)

  nx = x.shape[0]//2+1

  ipp = jnp.concatenate((ip[0:nx][::-1],ip))
  ip = jnp.convolve(ipp,beam,mode='same')[nx:]

  ip = ip*y2K_RJ(freq=freq,Te=T_electron)

  dx = (xi-x0)*jnp.cos(yi)
  dy  = yi-y0
  dr = jnp.sqrt(dx*dx + dy*dy)*180./np.pi*3600.

  return jnp.interp(dr,rmap,ip,right=0.)

@jax.jit
def potato_chip(p, xi, yi):
  #A shitty potato chip (hyperbolic parabaloid) model for removing scan signal from maps
  #Inputs: p, a parameter vector with entries A, the overall amplitude, c0, an overall offset
  #c1 and c2, linear slopes, c3 and c4, parabolic amplitudes, and theta, a rotation angle.
  # xi, yi are x and y vectors
  #
  # Outputs: a vector of values for this model given p at the xi, yi

  #A, c0, c1, c2, c3, c4, theta = p
  #A, c1, c2, c3, c4, theta = p
  c1, theta = p
  x1, x2 = jnp.cos(theta)*xi + yi*jnp.sin(theta), -1*jnp.sin(theta)*xi + jnp.cos(theta)*yi
  
  #return A*(c0 + c1*x1 + c2*x2 + x1**2/c3 - x2**2/c4)
  #return A*(1+c1*x1 + c2*x2 + x1**2/c3 - x2**2/c4)
  #return A*(c1*x1 + x1**2/c3 - x2**2/c4)
  return c1*x1
# ---------------------------------------------------------------

pars = jnp.array([0,0,1.,1.,1.5,4.3,0.7,3e14])
tods = jnp.array(np.random.rand(2,int(1e4)))

#Some convenient functions for returning predictions, gradients, and predictions+gradients 
@partial(jax.jit,static_argnums=(3,4,5,6,7,8,))
def val_conv_int_gnfw(p,tods,z,max_R=10.00,fwhm=9.,freq=90e9,T_electron=5.0,r_map=15.0*60,dr=0.5):
  return conv_int_gnfw(p,tods[0],tods[1],z,max_R,fwhm,freq,T_electron,r_map,dr)

@partial(jax.jit,static_argnums=(3,4,5,6,7,8,))
def jac_conv_int_gnfw_fwd(p,tods,z,max_R=10.00,fwhm=9.,freq=90e9,T_electron=5.0,r_map=15.0*60,dr=0.5):
  return jax.jacfwd(conv_int_gnfw,argnums=0)(p,tods[0],tods[1],z,max_R,fwhm,freq,T_electron,r_map,dr)

@partial(jax.jit,static_argnums=(3,4,5,6,7,8,))
def jit_conv_int_gnfw(p,tods,z,max_R=10.00,fwhm=9.,freq=90e9,T_electron=5.0,r_map=15.0*60,dr=0.5):
  pred = conv_int_gnfw(p,tods[0],tods[1],z,max_R,fwhm,freq,T_electron,r_map,dr)
  grad = jax.jacfwd(conv_int_gnfw,argnums=0)(p,tods[0],tods[1],z,max_R,fwhm,freq,T_electron,r_map,dr)

  return pred, grad

#Todo: an analytic expression for the potato gradient definitely exists, just needs to be derived + implemented
@jax.jit
def jac_potato_grad(p, tods):
  return jax.jacfwd(potato_chip, argnums = 0)(p, tods[0], tods[1])

@jax.jit
def jit_potato_full(p, tods):
  pred = potato_chip(p, tods[0], tods[1])
  grad = jax.jacfwd(potato_chip, argnums = 0)(p, tods[0], tods[1])

  return pred, grad

def helper():
  return jit_conv_int_gnfw(pars,tods,1.00)[0].block_until_ready()


if __name__ == '__main__':
    #If you actually run this script it fires off some performance tests
    toc = time.time(); val_conv_int_gnfw(pars,tods,1.00); tic = time.time(); print('1',tic-toc)
    toc = time.time(); val_conv_int_gnfw(pars,tods,1.00); tic = time.time(); print('1',tic-toc)

    toc = time.time(); jac_conv_int_gnfw_fwd(pars,tods,1.00); tic = time.time(); print('2',tic-toc)
    toc = time.time(); jac_conv_int_gnfw_fwd(pars,tods,1.00); tic = time.time(); print('2',tic-toc)

    toc = time.time(); jit_conv_int_gnfw(pars,tods,1.00); tic = time.time(); print('3',tic-toc)
    toc = time.time(); jit_conv_int_gnfw(pars,tods,1.00); tic = time.time(); print('3',tic-toc)

    pars = jnp.array([0,0,1.,1.,1.5,4.3,0.7,3e14])
    tods = jnp.array(np.random.rand(2,int(1e4)))

    print(timeit.timeit(helper,number=10)/10)

    
def helper_isobeta(params, tod):
    x = tod.info['dx']
    y = tod.info['dy']
    
    xy = [x, y]
    xy = jnp.asarray(xy)

    pred, derivs = jit_conv_isobeta(params, xy)

    derivs = jnp.moveaxis(derivs, 2, 0)

    return derivs, pred

def jit_conv_isobeta(
    p,
    tod,
    z,
    max_R=10.00,
    fwhm=9.0,
    r_map=15.0 * 60,
    dr=0.5
):
    pred = conv_isobeta(
        p,
        tod[0],
        tod[1],
        z,
        max_R,
        fwhm,
        r_map,
        dr
    )
    grad = jax.jacfwd(conv_isobeta, argnums=0)(
        p,
        tod[0],
        tod[1],
        z,
        max_R,
        fwhm,
        r_map,
        dr
    )

    return pred, grad

def conv_isobeta(
    p,
    xi,
    yi,
    z,
    max_R=10.00,
    fwhm=9.0,
    r_map=15.0 * 60,
    dr=0.5
):
    x0, y0, theta, beta, amp = p
    rmap, iff = _conv_isobeta(
        p,
        xi,
        yi,
        z,
        max_R=10.00,
        fwhm=9.0,
        r_map=15.0 * 60,
        dr=0.5
    )
    
    cosdec = np.cos(y0)
    
    delx = (x0-xi)*cosdec
    dely = y0-yi
    
    dr = jnp.sqrt(delx*delx + dely*dely)*180.0 / np.pi * 3600

    return jnp.interp(dr, rmap, iff, right=0.0)

def _conv_isobeta(
    p,
    xi,
    yi,
    z,
    max_R=10.00,
    fwhm=9.0,
    r_map=15.0 * 60,
    dr=0.5
):
    x0, y0, theta, beta, amp = p
    theta_inv = 1./theta
    theta_inv_sqr = theta_inv*theta_inv
    cosdec = np.cos(y0)
    cosdec_inv = 1./cosdec
    sindec = np.sin(y0)
    power = 0.5 - 1.5*beta
    
    dR = max_R / 2e3
    
    r = jnp.arange(0.00, max_R, dR) + dR / 2.00
    gg = theta_inv_sqr * r**2
    flux = amp*(gg + 1)**power
    
        
    
    #-------
    
    rmap = jnp.arange(1e-10, r_map, dr)
    r_in_Mpc = rmap * (jnp.interp(z, dzline, daline))
    #rr = jnp.meshgrid(r_in_Mpc, r_in_Mpc)
    #rr = jnp.sqrt(rr[0] ** 2 + rr[1] ** 2)
    ff = jnp.interp(r_in_Mpc, r, flux, right=0.0)

    XMpc = Xthom * Mparsec

    #mustang beam 
    x = jnp.arange(-1.5 * fwhm // (dr), 1.5 * fwhm // (dr)) * (dr)
    beam = jnp.exp(-4 * np.log(2) * x ** 2 / fwhm ** 2)
    beam = beam / jnp.sum(beam)

    nx = x.shape[0] // 2 + 1

    iff = jnp.concatenate((ff[0:nx][::-1], ff))
    iff_conv = jnp.convolve(iff, beam, mode="same")[nx:]

    return rmap, iff_conv





