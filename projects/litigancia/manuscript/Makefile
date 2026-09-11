TEX = data_descriptor.tex
PDF = data_descriptor.pdf

.PHONY: all clean check-tex

all: check-tex $(PDF)

check-tex:
	@if command -v pdflatex >/dev/null 2>&1; then \
		: ; \
	elif command -v tectonic >/dev/null 2>&1; then \
		: ; \
	else \
		echo "No LaTeX engine found (pdflatex or tectonic)."; \
		echo ""; \
		echo "pdflatex is NOT a Python package — do not run: uv add pdflatex"; \
		echo ""; \
		echo "Install one of these on macOS:"; \
		echo "  brew install --cask basictex    # smaller (~100 MB), then:"; \
		echo "  eval (fish_add_path /Library/TeX/texbin)   # fish"; \
		echo "  export PATH=\"/Library/TeX/texbin:\$$PATH\"  # bash"; \
		echo ""; \
		echo "  brew install tectonic           # single binary, no full TeX Live"; \
		echo ""; \
		echo "Then: make -C docs/manuscript"; \
		exit 1; \
	fi

$(PDF): $(TEX)
	@if command -v pdflatex >/dev/null 2>&1; then \
		pdflatex -interaction=nonstopmode $(TEX); \
		pdflatex -interaction=nonstopmode $(TEX); \
	elif command -v tectonic >/dev/null 2>&1; then \
		tectonic $(TEX) --outdir .; \
	fi

clean:
	rm -f *.aux *.log *.out *.toc *.fls *.fdb_latexmk
