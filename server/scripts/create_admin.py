from getpass import getpass

from sqlalchemy import select

from creative_server.config import get_settings
from creative_server.database import SessionLocal, create_schema
from creative_server.models import UsageLimit, User
from creative_server.security import hash_password, validate_password, validate_username


def main() -> None:
    create_schema()
    settings = get_settings()
    username = validate_username(input(f"管理员账号 [{settings.bootstrap_admin}]: ").strip() or settings.bootstrap_admin)
    display_name = input("显示名称 [管理员]: ").strip() or "管理员"
    password = validate_password(getpass("管理员密码（至少 12 位）: "), username)
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == username)):
            raise SystemExit("账号已经存在，未做任何修改。")
        user = User(username=username, display_name=display_name, password_hash=hash_password(password), role="admin", status="active")
        db.add(user); db.flush()
        db.add(UsageLimit(user_id=user.id, daily_tasks=1000, daily_credits=10_000_000, concurrent_tasks=10, allow_paid_models=True, allowed_models_json="[]"))
        db.commit()
    print("管理员已创建。现在只能由该管理员创建或批准其他账号。")


if __name__ == "__main__":
    main()
