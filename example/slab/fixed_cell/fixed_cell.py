from ase import Atom, Atoms
from ase.build import fcc110
from ase.calculators.emt import EMT
from minimahopping.minhop import Minimahopping

# Make the Pt 110 slab.
atoms = fcc110('Pt', (2, 2, 2), vacuum=7.)

# Add the Cu2 adsorbate.
adsorbate = Atoms([Atom('Cu', atoms[7].position + (0., 0., 2.5)),
                   Atom('Cu', atoms[7].position + (0., 0., 5.0))])
atoms.extend(adsorbate)

# Set the calculator.
atoms.calc = EMT()

with Minimahopping(atoms, mdmin=3, fixed_cell_simulation = True,verbose_output=True, T0=500, dt0=0.01, use_MPI=False) as mh:
    mh(totalsteps=50)
