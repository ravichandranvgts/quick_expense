#!/bin/bash
# Builds a fresh bench in CI: Frappe, ERPNext and India Compliance on version-16, plus
# this repository's Quick Expense, all installed on the site "test_site".
# Adapted from India Compliance's .github/helper/install.sh.

set -e

# stop early on syntax errors or merge-conflict markers
python -m compileall -q -f "${GITHUB_WORKSPACE}/quick_expense"
if grep -lr --exclude-dir=node_modules --exclude-dir=.git "^<<<<<<< " "${GITHUB_WORKSPACE}"; then
	echo "Found merge conflicts"
	exit 1
fi

cd ~ || exit

sudo apt update
sudo apt remove -y mysql-server mysql-client || true
sudo apt install -y libcups2-dev redis-server mariadb-client

pip install frappe-bench

git clone "https://github.com/frappe/frappe" --branch "$FRAPPE_BRANCH" --depth 1
bench init --skip-assets --frappe-path ~/frappe --python "$(which python)" frappe-bench

mkdir ~/frappe-bench/sites/test_site
cp "${GITHUB_WORKSPACE}/.github/helper/site_config.json" ~/frappe-bench/sites/test_site/

mariadb --host 127.0.0.1 --port 3306 -u root -ptravis -e "
	SET GLOBAL character_set_server = 'utf8mb4';
	SET GLOBAL collation_server = 'utf8mb4_unicode_ci';
"

cd ~/frappe-bench || exit

# only the web, worker and redis processes are needed
sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile

bench get-app erpnext --branch "$FRAPPE_BRANCH"
bench get-app india_compliance https://github.com/resilient-tech/india-compliance --branch "$FRAPPE_BRANCH"
bench get-app quick_expense "${GITHUB_WORKSPACE}"
bench setup requirements --dev

if [ "$BUILD_ASSETS" = "true" ]; then
	bench build
fi

bench use test_site
bench start &> ~/frappe-bench/bench_start.log &
bench reinstall --yes
bench --verbose install-app india_compliance quick_expense
bench --site test_site add-to-hosts
