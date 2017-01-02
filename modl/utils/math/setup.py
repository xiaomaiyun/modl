from distutils.extension import Extension

import numpy
from Cython.Build import cythonize


def configuration(parent_package='', top_path=None):
    from numpy.distutils.misc_util import Configuration

    config = Configuration('math', parent_package, top_path)

    extensions = [Extension('modl._utils.math.enet_proj',
                            sources=['modl/utils/math/enet_proj.pyx'],
                            include_dirs=[numpy.get_include()],
                            ),
                  ]
    config.ext_modules += cythonize(extensions)

    config.add_subpackage('tests')

    return config

if __name__ == '__main__':
    from numpy.distutils.core import setup
    setup(**configuration(top_path='').todict())