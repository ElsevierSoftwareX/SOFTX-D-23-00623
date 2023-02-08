from setuptools import setup, find_packages

        
setup(
    name='minimahopping',
    version='0.0.1',
    author='Marco Krummenacher, Moritz Gubler, Jonas Finkler, Stefan Goedecker',
    description='ASE Minimahopping',
    long_description='Minimahopping compatible to ASE',
    url='https://github.com/',
    keywords='development, setup, setuptools',
    python_requires='>=3.7, <4',
    packages=find_packages(include=['minimahopping', 'minimahopping.*']),
    install_requires=[
        'numpy',
        'ase',
        'scipy',
        'scikit-learn',
        'networkx',
        'mpi4py',
        'sqnm @ git+https://github.com/moritzgubler/vc-sqnm#egg=sqnm&subdirectory=src/python/',
        'dataclasses-json',
        'numba',
        'spglib'
    ],
    entry_points={
      'console_scripts': [
        'sortByEnergy=minimahopping.commandLineInterface.sortAtoms:main',
        'omfpdistance=minimahopping.commandLineInterface.omfpdistance:main',
        'standardizeLattice=minimahopping.commandLineInterface.standardizeLattice:main'
      ]
    }
)

