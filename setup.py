from setuptools import setup

install_requires = [
    "lib==0.15.3",
]

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
