from job_hunt.common.logger import PROJECT_LOGGER_NAME, get_logger


def test_package_modules_keep_their_own_name():
    assert get_logger("job_hunt.cli.job_commands").name == "job_hunt.cli.job_commands"


def test_outside_modules_are_put_under_the_project_logger():
    assert get_logger("env").name == f"{PROJECT_LOGGER_NAME}.env"
