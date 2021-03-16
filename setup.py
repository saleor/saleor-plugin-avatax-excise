from setuptools import setup

install_requires = []

setup(
    name="saleor-avalara-excise",
    version="1.0",
    packages=["excise"],
    package_dir={"excise": "excise"},
    install_requires=install_requires,
    entry_points={
        "saleor.plugins": ["excise = excise.plugin:AvataxExcisePlugin"]
    },
)
