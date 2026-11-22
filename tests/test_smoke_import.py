def test_package_imports() -> None:
    import immich_ftp_pixel_backup
    from immich_ftp_pixel_backup import config, db, main, repository

    assert immich_ftp_pixel_backup.__version__
    assert config.load_config
    assert db.apply_migrations
    assert main.main
    assert repository.SyncRepository
