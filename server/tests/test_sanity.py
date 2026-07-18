import introspect


def test_package_imports():
    assert introspect.__version__ == "0.1.0"
