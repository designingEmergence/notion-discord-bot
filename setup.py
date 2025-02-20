from setuptools import setup, find_packages

setup(
    name="notion-discord-rag",
    version="0.1",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "discord.py",
        "python-dotenv",
        "openai",
        "numpy"
    ]
)

