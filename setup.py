from setuptools import find_packages, setup

setup(
    name="repo-translator",
    version="0.1.0",
    description="Translate entire GitHub repositories cheaply using translation APIs + optional AI review",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Newnol",
    url="https://github.com/newnol/repo-translator",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "click>=8.0",
        "rich>=13.0",
        "pyyaml>=6.0",
        "gitpython>=3.1",
        "langdetect>=1.0",
        "deep-translator>=1.11",
        "googletrans==4.0.0-rc1",
        "requests>=2.28",
        "tqdm>=4.60",
    ],
    extras_require={
        "dev": ["pytest", "pytest-cov", "black", "ruff"],
    },
    entry_points={
        "console_scripts": [
            "repo-translator=repo_translator.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Internationalization",
    ],
)
