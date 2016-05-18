from setuptools import setup

setup(
    name='vpc.sh',
    version='0.1',
    author='Oleksandr Chyrko',
    author_email='aleksandr.chirko@gmail.com',
    py_modules=['vpc_sh'],
    install_requires=['Click', 'boto', 'fabric', 'tabulate'],
    entry_points='''
        [console_scripts]
        vpc.sh=vpc_sh:vpc_sh
    '''
)
