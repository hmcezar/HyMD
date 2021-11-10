import logging
import numpy as np
from mpi4py import MPI
from logger import Logger
import sympy
from field import comp_laplacian

def comp_pressure(
        phi,
        hamiltonian,
        velocities,
        config,
        phi_fourier,
        phi_laplacian,
        phi_transfer,
        args,
        bond_forces,
        angle_forces,
        positions,
        bond_pr,
        angle_pr,
        comm=MPI.COMM_WORLD
):
    rank = comm.Get_rank()
    size = comm.Get_size()

    V = np.prod(config.box_size)
    n_mesh__cells = np.prod(np.full(3, config.mesh_size))
    volume_per_cell = V / n_mesh__cells
    w = hamiltonian.w(phi) * volume_per_cell

    #Kinetic term
    kinetic_energy = 0.5 * config.mass * np.sum(velocities ** 2)
    p_kin = 2/(3*V)*kinetic_energy

    #Term 1
    p0 = -1/V * np.sum(w)
  
    #Term 2
    V_bar_tuple = [
        hamiltonian.V_bar[k](phi) for k in range(config.n_types)
    ]
    V_bar = [sum(list(V_bar_tuple[i])) for i in range(len(V_bar_tuple))]

    p1 = [
        1/V
        * V_bar[i]
        * phi[i] * volume_per_cell for i in range(config.n_types)
    ]
    p1 = [np.sum(p1[i].value) for i in range(config.n_types)]
    p1 = np.sum(p1)
    
    #Term 3
    comp_laplacian(
            phi_fourier,
            phi_transfer,
            phi_laplacian,
            config,
            )

    p2x = [
        1/V * config.sigma**2 * V_bar[i] * phi_laplacian[i][0] * volume_per_cell for i in range(config.n_types)
    ]
    p2y = [
        1/V * config.sigma**2 * V_bar[i] * phi_laplacian[i][1] * volume_per_cell for i in range(config.n_types)
    ]
    p2z = [
        1/V * config.sigma**2 * V_bar[i] * phi_laplacian[i][2] * volume_per_cell for i in range(config.n_types)
    ]

    #square gradient
    p_w1_0 = 0.0
    p_w1_1 = [0.0, 0.0, 0.0]
    p_w1_2 = [0.0, 0.0, 0.0]
    p2x = [np.sum(p2x[i].value) for i in range(config.n_types)]
    p2y = [np.sum(p2y[i].value) for i in range(config.n_types)]
    p2z = [np.sum(p2z[i].value) for i in range(config.n_types)]
    p2x = np.sum(p2x)
    p2y = np.sum(p2y)
    p2z = np.sum(p2z)

    #Bonded force term: linking 2 particles
    p_bond  = {
             'x': bond_pr[0]/V,
             'y': bond_pr[1]/V,
             'z': bond_pr[2]/V
             }

    #Angle force term: linking 3 particles
    p_angle =  {
             'x': angle_pr[0]/V,
             'y': angle_pr[1]/V,
             'z': angle_pr[2]/V
             }

    #Dihedral angle force term: linking 4 atoms
    p_dihedral = {
              'x': 0.0,                                              
              'y': 0.0,
              'z': 0.0
                  } 
    
    #Add formal parameter dihedral_forces as: comp_pressure(..., dihedral_forces)
    #Define dictionary:
    #forces = {                                                                                                         
    #          'x': dihedral_forces[:,0],                                                                                    
    #          'y': dihedral_forces[:,1],                                                                                    
    #          'z': dihedral_forces[:,2]                                                                                     
    #           }                                                                                                         
    # Compute the pressure due to dihedrals as:                                                                                                                     
    #p_dihedral = {
    #          'x': np.sum( np.multiply(forces['x'],positions[:,0]) )*(1/V),                                              
    #          'y': np.sum( np.multiply(forces['y'],positions[:,1]) )*(1/V),                                              
    #          'z': np.sum( np.multiply(forces['z'],positions[:,2]) )*(1/V)                                               
    #              } 



    #Total pressure in x, y, z
    p_tot = {
        'x': p_kin + p0 + p1 + p2x + p_w1_0 + p_w1_1[0] + p_w1_2[0] + p_bond['x'] + p_angle['x'] + p_dihedral['x'],
        'y': p_kin + p0 + p1 + p2y + p_w1_0 + p_w1_1[1] + p_w1_2[1] + p_bond['y'] + p_angle['y'] + p_dihedral['y'],
        'z': p_kin + p0 + p1 + p2z + p_w1_0 + p_w1_1[2] + p_w1_2[2] + p_bond['z'] + p_angle['z'] + p_dihedral['z']
            }

    return_value = [
                p_kin,p0,p1,                                                  #0-2
                p2x,p2y,p2z,                                                  #3-5
                p_w1_0,                                                       #6
                p_w1_1[0], p_w1_2[0], p_w1_1[1], p_w1_2[1], p_w1_1[2], p_w1_2[2],#7-12
                p_bond['x'], p_bond['y'], p_bond['z'],                        #13-15
                p_angle['x'], p_angle['y'], p_angle['z'],                     #16-18
                p_dihedral['x'], p_dihedral['y'], p_dihedral['z'],            #19-21
                p_tot['x'], p_tot['y'], p_tot['z']                            #22-24
    ]
    return_value = [comm.allreduce(_, MPI.SUM) for _ in return_value]
    #Total pressure across all ranks

    if config.barostat:
        beta = 4.6 * 10**(-5) #bar^(-1) #isothermal compressibility of water
        if config.barostat == 'semiisotropic':
            #L: Lateral; N: Normal
            [PL, PN] = [0, 0]
            PL = (return_value[-3] + return_value[-2])/2
            PN = return_value[-1]
            alphaL = 1 - config.time_step / config.tau_p * beta * (config.target_pressure - PL)
            alphaN = 1 - config.time_step / config.tau_p * beta * (config.target_pressure - PN)
        elif config.barostat == 'isotropic':
            P = np.average(return_value[-3:-1])
            alpha = 1 - config.time_step / config.tau_p * beta * (config.target_pressure - P)
    return return_value
