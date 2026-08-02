def pytest_itemcollected(item):
    # Show only the test function name (and param if any)
    item._nodeid = item.nodeid.split("::test_")[-1]
