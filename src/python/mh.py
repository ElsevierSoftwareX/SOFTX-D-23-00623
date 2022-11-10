import numpy as np
import scipy
import warnings
import os
import bisect
from ase.io import read, write
from ase.md.velocitydistribution import MaxwellBoltzmannDistribution
import lattice_operations as lat_opt
from OverlapMatrixFingerprint import OverlapMatrixFingerprint as OMFP
from copy import deepcopy
from soften import Softening
from md import MD
from optim import Opt
from minimum import Minimum
from cell_atom import Cell_atom
import time


"""
MH Software written by Marco Krummenacher (marco.krummenacher@unibas.ch)
Parts of the software were originally developped (some in Fortran) from other people:
  -- VCSMD: Martin Sommer-Joergenson
  -- VCS Softening: Hannes Huber
  -- VCS optimizer: Moritz Gubler
  -- OMFP in python: Jonas Finkler
"""


class Minimahopping:
    _default_settings = {
        'T0' : 2000.,  # Initital temperature in Kelvin (float)
        'beta_decrease': 1. / 1.1,  # temperature adjustment parameter (float)
        'beta_increase': 1.1,  # temperature adjustment parameter (float)
        'Ediff0' : .01, # Initial energy aceptance threshold (float)
        'alpha_a' : 0.95, # factor for decreasing Ediff (float)
        'alpha_r' : 1.05, # factor for increasing Ediff (float)
        'n_soft' : 20, # number of softening steps for the velocity before the MD (int)
        'ns_orb' : 1, # number of s orbitals in OMFP fingerprint
        'np_orb' : 1, # number of p orbitals in OMFP fingerprint
        'width_cutoff' : 3.5, # with cutoff for OMFP fingerprint
        'exclude': [], # Elements which are to be excluded from the OMFP (list of str)
        'dt' : 0.05, # timestep for the MD part (float)
        'mdmin' : 2, # criteria to stop the MD trajectory (no. of minima) (int)
        'fmax' : 0.000005, # max force component for the local geometry optimization
        'enhanced_feedback' : False, # Enhanced feedback to adjust the temperature (bool)
        'energy_threshold' : 0.00005, # Energy threshold at which a OMFP distance calculation is performed (float)
        'n_poslow' : 30, # Number of posmin files which are written in sorted order (int)
        'minima_threshold' : 5.e-3, # Fingerprint difference for identifying identical configurations (float)
        'restart_optim' : False, # Reoptimizes all the proviously found minima which are read (bool)
        'start_lowest': False, # If True the run is restarted with the lowest alredy known minimum
        'verbose' : True, # If True MD and optim. steps are written to the output (bool)
        'new_start': False, # If True the Run is restarted and written to different output folders
        'run_time': "infinit" # String in the format D-HH:MM:SS, if infinit set run_time to infinit
    }

    def __init__(self, atoms, **kwargs):
        """Initialize with an ASE atoms object and keyword arguments."""
        self._atoms = atoms
        for key in kwargs:
            if key not in self._default_settings:
                raise RuntimeError('Unknown keyword: %s' % key)
        for k, v in self._default_settings.items():
            setattr(self, '_%s' % k, kwargs.pop(k, v))

        self._temperature = self._T0
        self._Ediff = self._Ediff0

        self._counter = 0

    def __call__(self, totalsteps = None):
        self._startup()
        while (self._counter <= totalsteps):
            print("START HOPPING STEP")
            print("  Start escape loop")
            print("  ---------------------------------------------------------------")
            self._escape()
            print("  ---------------------------------------------------------------")
            print("  New minimum found!")
            self._acc_rej_step()
            self._hoplog()
            self._n_visits = self._in_history_fp()
            log_msg = "  New minimum has been found {:d} time(s)".format(self._n_visits)
            print(log_msg)
            print("  Write restart files")
            self._adj_temperature()
            self._update_data()
            self._write_poslow()
            self._history_log()
            self._atoms = deepcopy(self._atoms_cur)
            self._counter += 1
            print("DONE")
            print("=================================================================")

            _elapsed_time = time.time() - self._time_in

            if self._run_time is not "infinit":
                if _elapsed_time > self._run_time_sec:
                    msg = 'Simulation stopped because the given time is over\n'
                    msg += 'Run terminated after {:d} steps'.format(self._counter)
                    print(msg)
                    print("=================================================================")
                    return

        _elapsed_time = time.time() - self._time_in
        day = _elapsed_time // (24 * 3600)
        _elapsed_time = _elapsed_time % (24 * 3600)
        hour = _elapsed_time // 3600
        _elapsed_time %= 3600
        minutes = _elapsed_time // 60
        _elapsed_time %= 60
        seconds = _elapsed_time
        msg = 'Run terminated after {:d} steps in {:d}D {:d}H {:d}M {:d}S'.format(totalsteps,
                                                                                int(day),
                                                                                int(hour),
                                                                                int(minutes),
                                                                                int(seconds))
        print(msg)

    def _startup(self):
        print("=================================================================")
        print("MINIMAHOPPING SETUP START")

        # Convert given time to seconds
        if self._run_time is not "infinit":
            self._run_time_sec = self._get_sec()
        self._time_in = time.time()

        # Check if run is restarted
        self.all_minima = []
        self.all_minima_sorted = []
        self.unique_minima = []
        self.accepted_minima = []
        self.intermediate_minima = []
        self._i_step = 0

        self._n_min = 1
        self._n_unique = 0
        self._n_notunique = 0
        self._n_same = 0

        self._outpath = 'output/'
        if not os.path.exists(self._outpath):
            os.mkdir(self._outpath)

        self._minima_path = 'minima/'
        if not os.path.exists(self._minima_path):
            os.mkdir(self._minima_path)

        _is_acc_minima = os.path.exists(self._outpath + 'acc.extxyz')
        _is_unique_minima = os.path.exists(self._outpath + 'min.extxyz')
        _is_history = os.path.exists(self._outpath + 'history.dat')
        if _is_unique_minima and _is_history:
            _is_restart = True
            if self._new_start:
                _is_restart = False
        else:
            is_files = {_is_history, _is_unique_minima}
            assert len(is_files)==1, 'Some but not all files exist for a restart.'
            _is_restart = False

        if _is_restart:
            msg = '  Restart of previous run'
            print(msg)
            calc = self._atoms.calc

            unique_minima = read(self._outpath + "min.extxyz", index=':')
            if _is_acc_minima:
                accepted_minima = read(self._outpath + "acc.extxyz", index=':')
                self._atoms = accepted_minima[-1]
            else:
                warn_msg = 'No previous accepted minima detected restart at last found minimum'
                warnings.warn(warn_msg, UserWarning)
                self._atoms = unique_minima[-1]

            if os.path.exists(self._outpath + 'fp.dat') and not self._restart_optim:
                fps = self._read_fp()
                assert len(fps) == len(unique_minima), 'FP and minima file have not the same length, delete fp file for fp recalculation'

                for fp, atom in zip(fps, unique_minima):
                    self.all_minima.append(
                        Minimum(deepcopy(atom), n_visit=1, fingerprint=fp, T=-100.0, ediff=-10., acc_rej='NA'))
                    self.all_minima_sorted.append(
                        Minimum(deepcopy(atom), n_visit=1, fingerprint=fp, T=-100.0, ediff=-10., acc_rej='NA'))
            else:
                warn_msg = 'Fingerprints all recalculated for all found minima'
                warnings.warn(warn_msg, UserWarning)
                for atom in unique_minima:
                    if self._restart_optim:
                        atom.calc = calc
                        _positions, _lattice = self._restart_opt(atom)
                        atom.set_positions(_positions)
                        atom.set_cell(_lattice)

                    fp = self._get_OMFP(atom, s=self._ns_orb, p=self._np_orb, width_cutoff=self._width_cutoff)
                    self.all_minima.append(
                        Minimum(deepcopy(atom), n_visit=1, fingerprint=fp, T=-100.0, ediff=-10., acc_rej='NA'))
                    self.all_minima_sorted.append(
                        Minimum(deepcopy(atom), n_visit=1, fingerprint=fp, T=-100.0, ediff=-10., acc_rej='NA'))


            if self._restart_optim:
                _positions, _lattice = self._restart_opt(self._atoms)
                self._atoms.set_positions(_positions)
                self._atoms.set_cell(_lattice)

            self.all_minima_sorted.sort()
            if self._start_lowest:
                self._atoms = self.all_minima_sorted[0].atoms

            self._atoms.calc = calc

            _history_file = open(self._outpath + 'history.dat', 'r')
            self.history = []
            for line in _history_file:
                self.history.append(line)
            _last_line = self.history[-1].split()
            self._temperature = float(_last_line[2])
            self._Ediff = float(_last_line[3])
            _history_file.close()
        else:
            msg = '  New MH run is started'
            print(msg)
            _positions, _lattice = self._restart_opt(self._atoms)
            self._atoms.set_positions(_positions)
            self._atoms.set_cell(_lattice)
            write(self._outpath + "acc.extxyz", self._atoms, append=True)

        self._atoms_cur = deepcopy(self._atoms)
        self._n_visits = 1
        self._acc_rej = 'Initial'
        self._history_log()

        print("DONE")
        print("=================================================================")




    def _restart_opt(self, atoms, ):
        _atoms = deepcopy(atoms)
        opt = Opt(atoms=_atoms, outpath=self._outpath,max_froce_threshold=self._fmax, verbose=self._verbose)
        if True in self._atoms.pbc:
            _positions, _lattice, _noise = opt.run()
        else:
            _positions, _noise = opt.run()
            _lattice = np.zeros((3,3))
        return _positions, _lattice

    def _get_sec(self,):
        """Get seconds from time."""
        nd, d = self._run_time.split('-')
        h, m, s = d.split(':')
        return int(nd) * 86400 + int(h) * 3600 + int(m) * 60 + int(s)


    def _read_fp(self):
        fp_file = open(self._outpath + 'fp.dat', 'r')
        fps = []
        if True in self._atoms.pbc:
            fp_array = []
            nat = len(self._atoms)
            for i, line in enumerate(fp_file):
                fp_vector = []
                vector = line.split()
                for num in vector:
                    fp_vector.append(float(num))
                fp_vector = np.array(fp_vector)
                fp_array.append(fp_vector)
                if (i+1)%(nat) == 0:
                    fp_array = np.array(fp_array)
                    fps.append(fp_array)
                    fp_array = []
        else:
            for line in fp_file:
                fp_vector = []
                vector = line.split()
                for num in vector:
                    fp_vector.append(float(num))
                fp_vector = np.array(fp_vector)
                fps.append(fp_vector)
        fp_file.close()
        return fps




    def _escape(self):
        """
        Escape loop to find a new minimum
        """
        _escape = 0.0
        _escape_energy = 0.0
        _fp_in = self._get_OMFP(self._atoms,s=self._ns_orb, p=self._np_orb, width_cutoff=self._width_cutoff)
        _energy_in = self._atoms.get_potential_energy()
        _beta_s = 1.05
        _temperature_in = self._temperature
        _i_steps = 0
        while _escape < self._minima_threshold or _escape_energy < self._energy_threshold:

            if _i_steps > 0:
                self._n_same += 1
                self._acc_rej = 'Same'
                self._history_log()
                self._temperature *= _beta_s
                log_msg = "    Same minimum found with fpd {:1.2e} {:d} time(s). Increase temperature to {:1.5f}".format(_escape, self._n_same, self._temperature)
                print(log_msg)

            MaxwellBoltzmannDistribution(self._atoms, temperature_K=self._temperature)

            if True in self._atoms.pbc:
                _mass = .75 * np.sum(self._atoms.get_masses()) / 10.
                self._cell_atoms = Cell_atom(mass=_mass, positions=self._atoms.get_cell())
                self._cell_atoms.set_velocities_boltzmann(temperature=self._temperature)

                softening = Softening(self._atoms, self._cell_atoms)
                _velocities, _cell_velocities = softening.run(self._n_soft)
                self._atoms.set_velocities(_velocities)
                self._cell_atoms.velocities = _cell_velocities

                print("    VCS MD Start")

                md = MD(atoms=self._atoms, outpath=self._outpath, cell_atoms=self._cell_atoms, dt=self._dt, n_max=self._mdmin, verbose=self._verbose)
                _positions, _cell , self._dt = md.run()

                log_msg = "    VCS MD finished after {:d} steps visiting {:d} maxima. New dt is {:1.5f}".format(md._i_steps, self._mdmin, self._dt)
                print(log_msg)
                self._atoms.set_positions(_positions)
                self._atoms.set_cell(_cell)
                lat_opt.reshape_cell2(self._atoms, 6)

                print("    VCS OPT start")

                opt = Opt(atoms=self._atoms, outpath=self._outpath, max_froce_threshold=self._fmax, verbose=self._verbose)
                _positions, _lattice, self._noise = opt.run()
                self._atoms.set_positions(_positions)
                self._atoms.set_cell(_lattice)

                log_msg = "    VCS OPT finished after {:d} steps".format(opt._i_step)
                print(log_msg)

            else:
                softening = Softening(self._atoms)
                _velocities = softening.run(self._n_soft)
                self._atoms.set_velocities(_velocities)

                print("    MD Start")

                md = MD(atoms=self._atoms, outpath=self._outpath, cell_atoms=None, dt=self._dt, n_max=self._mdmin, verbose=self._verbose)
                _positions , self._dt= md.run()
                self._atoms.set_positions(_positions)

                log_msg = "    MD finished after {:d} steps visiting {:d} maxima. New dt is {:1.5f} ".format(md._i_steps, self._mdmin, self._dt)
                print(log_msg)
                print("    OPT start")
                opt = Opt(atoms=self._atoms, outpath=self._outpath, max_froce_threshold=self._fmax, verbose=self._verbose)
                _positions, self._noise = opt.run()
                self._atoms.set_positions(_positions)
                log_msg = "    VCS OPT finished after {:d} steps".format(opt._i_step)
                print(log_msg)

            self._check_energy_threshold()

            _fp_out = self._get_OMFP(self._atoms, s=self._ns_orb, p=self._np_orb, width_cutoff=self._width_cutoff)
            _energy_out = self._atoms.get_potential_energy()
            self._fp = _fp_out
            _escape = self.fp_distance(_fp_in, _fp_out) / _fp_out.shape[0]
            _escape_energy = abs(_energy_in - _energy_out)

            write(self._outpath + 'locm.extxyz', self._atoms, append=True)

            _i_steps += 1
            self._n_min += 1

        log_msg = "    New minimum found with fpd {:1.2e} after looping {:d} time(s)".format(_escape, _i_steps)
        print(log_msg)

        self.intermediate_minima.append(deepcopy(self._atoms))
        self._acc_rej = 'Inter'
        self._history_log()

        #self._temperature = _temperature_in


    def _hoplog(self):
        self._i_step += 1
        log_msg = "  Epot:  {:1.5f}   E_diff:  {:1.5f}    Temp:   {:1.5f} ".format(self._atoms.get_potential_energy(),
                                                                                             self._Ediff,
                                                                                             self._temperature)
        print(log_msg)



    def _acc_rej_step(self,):
        _e_pot_cur = self._atoms_cur.get_potential_energy()
        idebug = 0
        for atom in self.intermediate_minima:
            _e_pot = atom.get_potential_energy()
            idebug += 1
            if  _e_pot - _e_pot_cur < self._Ediff:
                self._Ediff *= self._alpha_a
                self._atoms_cur = deepcopy(atom)
                self._acc_rej = "Accepted"
                self.intermediate_minima = []
                ediff_acc = _e_pot - _e_pot_cur
            else:
                self._Ediff *= self._alpha_r
                self._acc_rej = "Rejected"
                ediff_rej = _e_pot - _e_pot_cur

        if self._acc_rej == "Accepted":
            log_msg = "  Minimum was accepted:  Enew - Ecur = {:1.5f} < {:1.5f} = Ediff".format(ediff_acc,
                                                                                                self._Ediff)
            print(log_msg)
        else:
            log_msg = "  Minimum was rejected:  Enew - Ecur = {:1.5f} > {:1.5f} = Ediff".format(ediff_rej,
                                                                                                self._Ediff)
            print(log_msg)

    def _in_history_fp(self,):
        mini = Minimum(deepcopy(self._atoms),
                       n_visit=-1,
                       fingerprint=self._fp,
                       T=self._temperature,
                       ediff=self._Ediff,
                       acc_rej=self._acc_rej)
        _i_start = bisect.bisect(self.all_minima_sorted, mini)
        _epot = self._atoms.get_potential_energy()
        _fp1 = self._fp
        i = 1

        #backward
        _energy_difference = 0
        _i_compare = _i_start
        while _energy_difference < self._energy_threshold:
            _i_compare -= 1
            if _i_compare < 0:
                break
            else:
                s = self.all_minima_sorted[_i_compare]
                _energy_difference = abs(s.atoms.get_potential_energy() - _epot)
                _fp2 = s.fingerprint
                _fp_dist = self.fp_distance(_fp1, _fp2) / _fp2.shape[0]
                if _fp_dist < self._minima_threshold:
                    i += 1


        #forward
        _i_compare = _i_start
        while _energy_difference < self._energy_threshold:
            _i_compare += 1
            if _i_compare+1 > len(self.all_minima_sorted):
                break
            else:
                s = self.all_minima_sorted[_i_compare]
                _energy_difference = abs(s.atoms.get_potential_energy() - _epot)
                _fp2 = s.fingerprint
                _fp_dist = self.fp_distance(_fp1, _fp2) / _fp2.shape[0]
                if _fp_dist < self._minima_threshold:
                    i += 1
        return i

    def _adj_temperature(self,):
        if self._n_visits > 1:
            self._n_notunique += 1
            if self._enhanced_feedback:
                self._temperature = self._temperature * self._beta_increase * (1. + 1. * np.log(float(self._n_visits)))
            else:
                self._temperature = self._temperature * self._beta_increase
        else:
            self._n_unique += 1
            self._temperature = self._temperature * self._beta_decrease
            self.unique_minima.append(deepcopy(self._atoms))
            write(self._outpath + "min.extxyz", self.unique_minima[-1],  append=True)
            self._write_fp()


    def _write_fp(self):
        fp_file = open(self._outpath + 'fp.dat', 'a')
        if True in self._atoms.pbc:
            for env in self._fp:
                for num in env:
                    fp_w = str(num) + '  '
                    fp_file.write(fp_w)
                fp_file.write('\n')
        else:
            for num in self._fp:
                fp_w = str(num) + '  '
                fp_file.write(fp_w)
            fp_file.write('\n')
        fp_file.close()




    def _update_data(self):
        if self._acc_rej == "Accepted":
            self.accepted_minima.append(deepcopy(self._atoms))
            write(self._outpath + "acc.extxyz", self.accepted_minima[-1], append=True)

        mini = Minimum(deepcopy(self._atoms),
                                       n_visit=self._n_visits,
                                       fingerprint=self._fp,
                                       T=self._temperature,
                                       ediff=self._Ediff,
                                       acc_rej=self._acc_rej)
        self.all_minima.append(mini)
        bisect.insort(self.all_minima_sorted, mini)

    def _history_log(self):
        _notunique_frac = float(self._n_notunique)/float(self._n_min)
        _same_frac = float(self._n_same)/float(self._n_min)
        _unique_frac = 1. - (_notunique_frac+_same_frac)

        history_msg = "{:1.9f}  {:d}  {:1.5f}  {:1.5f}  {:1.2f}  {:1.2f}  {:1.2f} {:s} \n".format(self._atoms.get_potential_energy(),
                                                                        self._n_visits,
                                                                        self._temperature,
                                                                        self._Ediff,
                                                                        _same_frac,
                                                                        _notunique_frac,
                                                                        _unique_frac,
                                                                        self._acc_rej)

        history_file = open(self._outpath + 'history.dat', 'a')
        history_file.write(history_msg)
        history_file.close()
        self._n_visits = 0


    def _check_energy_threshold(self):
        if self._energy_threshold < self._noise:
            _warning_msg = 'Energy threshold is below the noise level'
            warnings.warn(_warning_msg, UserWarning)


    def _write_poslow(self,):
        _i_poslow = 0
        for s in self.all_minima_sorted:
            if s.n_visit == 1:
                filename = 'min'+str(_i_poslow).zfill(6)
                if True in s.atoms.pbc:
                    filename += '.ascii'
                else:
                    filename += '.xyz'
                filename = self._minima_path + filename
                write(filename,s.atoms)
                _i_poslow += 1
            if _i_poslow-1 > self._n_poslow:
                break


    def _get_OMFP(self, _atoms,s=1, p=0, width_cutoff=1.5, maxnatsphere=100):
        """
        Calculation of the Overlapmatrix fingerprint. For peridoic systems a local environment fingerprint is calculated
        and a hungarian algorithm has to be used for the fingerprint distance. For non-periodic systems a global fingerprint
        is calculated and a simple l2-norm is sufficient for as a distance measure.

        If you use that function please reference:

        @article{sadeghi2013metrics,
        title={Metrics for measuring distances in configuration spaces},
        author={Sadeghi, Ali and Ghasemi, S Alireza and Schaefer, Bastian and Mohr, Stephan and Lill, Markus A and Goedecker, Stefan},
        journal={The Journal of chemical physics},
        volume={139},
        number={18},
        pages={184118},
        year={2013},
        publisher={American Institute of Physics}
        }

        and

        @article{zhu2016fingerprint,
        title={A fingerprint based metric for measuring similarities of crystalline structures},
        author={Zhu, Li and Amsler, Maximilian and Fuhrer, Tobias and Schaefer, Bastian and Faraji, Somayeh and Rostami, Samare and Ghasemi, S Alireza and Sadeghi, Ali and Grauzinyte, Migle and Wolverton, Chris and others},
        journal={The Journal of chemical physics},
        volume={144},
        number={3},
        pages={034203},
        year={2016},
        publisher={AIP Publishing LLC}
        }

        Input:
            s: int
                number of s orbitals for which the fingerprint is calculated
            p: int
                number of p orbitals for which the fingerprint is calculated
            width_cutoff: float
                cutoff for searching neighbouring atoms
            maxnatsphere:
                maximum of the neighboring atoms which can be in the sphere
        Return:
            omfp: np array
                numpy array which contains the fingerprint
        """

        _pbc = list(set(_atoms.pbc))
        assert len(_pbc) == 1, "mixed boundary conditions"
        _ang2bohr = 1.8897161646320724

        _symbols = _atoms.get_chemical_symbols()
        _positions = _atoms.get_positions()
        _elements = _atoms.get_atomic_numbers()
        _selected_postions = []
        _selected_elem = []

        for symb,elem, pos in zip(_symbols, _elements,_positions):
            if symb not in self._exclude:
                _selected_postions.append(pos)
                _selected_elem.append(elem)
        _selected_postions = np.array(_selected_postions)


        if True in _pbc:
            _selected_positions = _selected_postions*_ang2bohr
            _lattice = _atoms.get_cell()*_ang2bohr
            _omfpCalculator = OMFP.stefansOMFP(s=s, p=p, width_cutoff=width_cutoff, maxnatsphere=maxnatsphere)
            _omfp = _omfpCalculator.fingerprint(_selected_positions, _selected_elem, lat=_lattice)
            _omfp = np.array(_omfp)

        else:
            _selected_positions = _selected_postions*_ang2bohr
            _elements = _atoms.get_atomic_numbers()
            _width_cutoff = 1000000
            _maxnatsphere = len(_atoms)
            _omfpCalculator = OMFP.stefansOMFP(s=s, p=p, width_cutoff=_width_cutoff, maxnatsphere=_maxnatsphere)
            _omfp = _omfpCalculator.globalFingerprint(_selected_positions, _selected_elem)
            _omfp = np.array(_omfp)

        return _omfp

    def fp_distance(self, desc1, desc2):
        """
        Calcualtes the fingerprint distance of 2 structures with local environment descriptors using the hungarian algorithm
        if a local environment descriptor is used. Else the distance is calculated using l2-norm.
        desc1: np array
            numpy array containing local environments of structure 1
        desc2: np array
            numpy array containing local environments of structure 2
        Return:
            Global fingerprint distance between structure 1 and structure 2
        """

        n_dim1 = len(desc1.shape)
        n_dim2 = len(desc2.shape)

        assert n_dim1 == n_dim2, "Dimension of vector 1 is and vector 2 is different"
        assert n_dim1 < 3, "Dimension of vector 1 is larger that 2"
        assert n_dim2 < 3, "Dimension of vector 2 is larger that 2"

        if n_dim1 == 1 and n_dim2 == 1:
            fp_dist = np.linalg.norm(desc1 - desc2)
        else:
            costmat = self._costmatrix(desc1, desc2)
            ans_pos = scipy.optimize.linear_sum_assignment(costmat)
            fp_dist = 0.
            for index1, index2 in zip(ans_pos[0], ans_pos[1]):
                fp_dist += np.dot((desc1[index1, :] - desc2[index2, :]), (desc1[index1, :] - desc2[index2, :]))
            fp_dist = np.sqrt(fp_dist)

        return fp_dist

    def _costmatrix(self, desc1, desc2):
        """
        Cost matrix of the local fingerprints for the hungarian algorithm
        desc1: np array
            numpy array containing local fingerprints of structure 1
        desc2: np array
            numpy array containing local fingerprints of structure 2
        Return:
            cost matrix of with the distances of the local fingerprints
        """
        assert desc1.shape[0] == desc2.shape[0], "descriptor has not the same length"

        costmat = np.zeros((desc1.shape[0], desc2.shape[0]))

        for i, vec1 in enumerate(desc1):
            for j, vec2 in enumerate(desc2):
                costmat[i, j] = np.linalg.norm(vec1 - vec2)

        return costmat