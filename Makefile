.PHONY: run build deb test clean backup install-deps

run:
	source finances/bin/activate && python main.py

test:
	source finances/bin/activate && \
	pytest tests/ -v --asyncio-mode=auto \
	--cov=backend --cov-report=term-missing

build:
	bash scripts/build.sh

deb: build
	bash scripts/build_deb.sh

backup:
	source finances/bin/activate && \
	python scripts/backup.py

clean:
	rm -rf build/ dist/ __pycache__/
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + \
	2>/dev/null || true

install-deps:
	source finances/bin/activate && \
	pip install -r requirements.txt
