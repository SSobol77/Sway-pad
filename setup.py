from setuptools import setup, find_packages

setup(
    name='sway-pad',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        'pygments', 
        'curses', 
        'toml', 
        'pylint'
    ],
    entry_points={
        'console_scripts': [
            'sway-pad=sway_pad.sway:main'
        ]
    },
    include_package_data=True,
    package_data={'': ['config.toml']},
    data_files=[('config', ['sway_pad/config.toml'])],
)
