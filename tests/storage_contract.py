from workflow_v2.storage import (
    InvalidStoragePath,
    StorageAlreadyExists,
    StorageNotFound,
    StorageVersionConflict,
)


def exercise_backend_contract(testcase, factory):
    storage = factory()

    with testcase.assertRaises(StorageNotFound):
        storage.read("missing.bin")
    with testcase.assertRaises(StorageNotFound):
        storage.write_if_version("missing.bin", b"new", "missing-version")
    with testcase.assertRaises(StorageNotFound):
        storage.delete_if_version("missing.bin", "missing-version")

    first = b"alpha\x00beta\n"
    first_version = storage.create_if_absent("nested/state.bin", first)
    loaded = storage.read("nested/state.bin")
    testcase.assertEqual(loaded.content, first)
    testcase.assertEqual(loaded.version, first_version)

    with testcase.assertRaises(StorageAlreadyExists):
        storage.create_if_absent("nested/state.bin", b"replacement")
    testcase.assertEqual(storage.read("nested/state.bin").content, first)

    second = b"second\n"
    second_version = storage.write_if_version("nested/state.bin", second, first_version)
    testcase.assertEqual(storage.read("nested/state.bin").content, second)
    testcase.assertEqual(storage.read("nested/state.bin").version, second_version)

    with testcase.assertRaises(StorageVersionConflict):
        storage.write_if_version("nested/state.bin", b"stale writer\n", first_version)
    winner = storage.read("nested/state.bin")
    testcase.assertEqual(winner.content, second)
    testcase.assertEqual(winner.version, second_version)

    delete_base = storage.create_if_absent("claims/current.json", b"claim-v1\n")
    delete_current = storage.write_if_version(
        "claims/current.json", b"claim-v2\n", delete_base
    )
    with testcase.assertRaises(StorageVersionConflict):
        storage.delete_if_version("claims/current.json", delete_base)
    testcase.assertEqual(
        storage.read("claims/current.json").version,
        delete_current,
    )
    storage.delete_if_version("claims/current.json", delete_current)
    with testcase.assertRaises(StorageNotFound):
        storage.read("claims/current.json")

    storage.create_if_absent("a/one.txt", b"one")
    storage.create_if_absent("a/deep/two.txt", b"two")
    storage.create_if_absent("b/three.txt", b"three")
    testcase.assertEqual(
        storage.list(),
        ["a/deep/two.txt", "a/one.txt", "b/three.txt", "nested/state.bin"],
    )
    testcase.assertEqual(storage.list("a"), ["a/deep/two.txt", "a/one.txt"])
    testcase.assertEqual(storage.list("a/one.txt"), ["a/one.txt"])
    testcase.assertEqual(storage.list("not-there"), [])

    for unsafe in ("", "/absolute", "..", "a/../b", "a\\b", "a//b", "."):
        with testcase.subTest(path=unsafe):
            with testcase.assertRaises(InvalidStoragePath):
                storage.read(unsafe)
