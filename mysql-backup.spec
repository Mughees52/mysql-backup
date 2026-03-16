Name:           mysql-backup
Version:        0.1.0
Release:        1%{?dist}
Summary:        MySQL/MariaDB backup suite with logical, physical, and binlog backups

License:        Apache-2.0
URL:            https://example.com/mysql-backup
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  python3-setuptools

Requires:       python3
Requires:       python3-pyyaml
Requires:       python3-dateutil
Requires:       python3-pymysql
Requires:       python3-cryptography
Requires:       mydumper
Requires:       mysql-client
Requires:       rsync
Requires:       gpg

%description
mysql-backup is a Python 3 backup suite for MySQL/MariaDB providing:
- Logical backups via mydumper
- Physical backups via xtrabackup / mariadb-backup
- Binlog backups via mysqlbinlog
- Encryption, deduplication, disk-space checks, PXC desync, and offsite copies (S3, rsync, GCS).

%prep
%autosetup -n %{name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install

%files
%license LICENSE
%doc README.md
/usr/bin/mysql_backup_driver
/usr/bin/mysql_backup_precheck
%{python3_sitelib}/mysql_msp_backup*

%changelog
* Mon Mar 16 2026 Mughees <you@example.com> - 0.1.0-1
- Initial RPM release

