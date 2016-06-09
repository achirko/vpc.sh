from setuptools import setup

try:
    import pypandoc
    readme = pypandoc.convert('README.md', 'rst')
except(IOError, ImportError):
    readme = open('README.md').read()

setup(
    name='vpc.sh',
    version='0.1',
    description="CLI tool to run shell commands on ec2 instances.",
    long_description=readme,
    url="https://github.com/achirko/vpc.sh",
    author='Oleksandr Chyrko',
    author_email='aleksandr.chirko@gmail.com',
    py_modules=['vpc_sh'],
    install_requires=['Click', 'boto', 'fabric', 'tabulate'],
    entry_points='''
        [console_scripts]
        vpc.sh=vpc_sh:vpc_sh
    ''',
    classifiers=[
          'Environment :: Console',
          'Intended Audience :: Developers',
          'Intended Audience :: System Administrators',
          'License :: OSI Approved :: BSD License',
          'Operating System :: Unix',
          'Operating System :: POSIX',
          'Programming Language :: Python',
          'Programming Language :: Python :: 2.7',
          'Topic :: System :: Clustering',
          'Topic :: System :: Systems Administration',
    ],
)
