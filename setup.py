from setuptools import setup, find_packages

setup(
    name="omotion-core",
    version="0.1.0",
    description=".motion format focused shared core library - template parser, renderer, quality gate, fragment manager, param manager",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="O.motion Contributors",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "jsonschema>=4.0.0",
        "pyyaml>=6.0",
    ],
    extras_require={
        "render": [
            "playwright>=1.40.0",
        ],
        "dev": [
            "pytest>=7.0",
            "pytest-cov",
        ],
    },
    entry_points={
        "console_scripts": [
            "omotion=cli.omotion:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Multimedia :: Video",
    ],
)
