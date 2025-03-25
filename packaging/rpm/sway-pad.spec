Name:           sway-pad
Version:        0.1.0
Release:        1%{?dist}
Summary:        Advanced text editor with syntax highlighting
License:        GPLv3
URL:            https://github.com/yourusername/sway-pad
BuildArch:      noarch
Requires:       python3-pygments python3-toml pylint python3-curses
%description
Advanced text editor with syntax highlighting and multithreading
%files
/usr/bin/sway-pad
/etc/sway-pad/config.toml
%license LICENSE
