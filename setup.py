from setuptools import setup, find_packages

setup(
    name='sway-pad',
    version='0.1.0',
    description='Advanced text editor with syntax highlighting and multithreading',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    author='Siergej Sobolewski',
    author_email='s.sobolewski@hotmail.com',
    url='https://github.com/yourusername/sway-pad',
    packages=find_packages(),
    install_requires=[
        'pygments>=2.13.0',
        'toml>=0.10.2',
        'pylint>=3.0.0',
        'curses>=2.2.1',
    ],
    entry_points={
        'console_scripts': [
            'sway-pad = sway_pad.sway:main'
        ]
    },
    include_package_data=True,
    package_data={'': ['config.toml']},
    data_files=[('config', ['sway_pad/config.toml'])],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Programming Language :: Python :: 3.11',
    ],
    python_requires='>=3.11',
    license='GPLv3',
)
