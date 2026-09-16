# This makefile provides tools for developers.
#
# Note that it requires you to have a .env file defined with the path to your manage.py file
# The syntax is: MANAGE_PY_PATH = /path/to/manage.py

-include .env
export

appname = aa-structuretimers
package = structuretimers

help:
	@echo "Makefile for $(appname)"

makemessages:
	cd $(package) && \
	django-admin makemessages \
		-l de \
		-l en \
		-l es \
		-l fr \
		-l it \
		-l ja \
		-l ko \
		-l ko_KR \
		-l ru \
		-l uk \
		-l zh_Hans \
		--keep-pot \
		--ignore 'build/*'

tx_push:
	tx push --source

tx_pull:
	tx pull -f

compilemessages:
	cd $(package) && \
	django-admin compilemessages \
		-l de \
		-l en \
		-l es \
		-l fr \
		-l it \
		-l ja \
		-l ko \
		-l ko_KR \
		-l ru \
		-l uk \
		-l zh_Hans

coverage:
	coverage run $(MANAGE_PY_PATH) test $(package) --keepdb --failfast && coverage html && coverage report -m

pylint:
	pylint --load-plugins pylint_django $(package)

graph_models:
	python $(MANAGE_PY_PATH) graph_models $(package) --arrow-shape normal -o $(appname)_models.png
