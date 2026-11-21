"""Row-level security in the Supabase migration, checked on a real Postgres (with a small Supabase stand-in)."""
import os
import uuid

import psycopg
import pytest

PG = os.environ.get("FASLA_TEST_PG", "postgresql://postgres@/fasla_rls?host=/tmp&port=54329")
ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="module")
def db():
    admin = PG.replace("/fasla_rls", "/postgres")
    try:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute("drop database if exists fasla_rls")
            c.execute("create database fasla_rls")
    except psycopg.OperationalError:
        pytest.skip("local Postgres not running")
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute(open(os.path.join(ROOT, "tests", "supabase_stub.sql")).read())
        c.execute(open(os.path.join(ROOT, "supabase", "migrations", "0001_fasla_init.sql")).read())
        a, b = uuid.uuid4(), uuid.uuid4()
        c.execute("insert into auth.users(id,email) values (%s,'a@x.com'),(%s,'b@x.com')", (a, b))
        ba = c.execute("insert into brands(user_id,name) values (%s,'A brand') returning id", (a,)).fetchone()[0]
        bb = c.execute("insert into brands(user_id,name) values (%s,'B brand') returning id", (b,)).fetchone()[0]
        c.execute("insert into plans(brand_id,user_id,status) values (%s,%s,'done')", (ba, a))
        c.execute("insert into plans(brand_id,user_id,status) values (%s,%s,'done')", (bb, b))
    yield dict(a=a, b=b, ba=ba, bb=bb)


def as_user(uid):
    c = psycopg.connect(PG)
    c.execute("set role authenticated")
    c.execute("select set_config('request.jwt.claim.sub', %s, false)", (str(uid),))
    return c


def test_user_sees_only_own_rows(db):
    with as_user(db["a"]) as c:
        names = [r[0] for r in c.execute("select name from brands")]
        assert names == ["A brand"]
        assert c.execute("select count(*) from plans").fetchone()[0] == 1


def test_cannot_insert_brand_for_someone_else(db):
    with as_user(db["a"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into brands(user_id,name) values (%s,'sneaky')", (db["b"],))


def test_cannot_update_or_delete_others(db):
    with as_user(db["a"]) as c:
        c.execute("update brands set name='hacked' where id=%s", (db["bb"],))
        c.execute("delete from brands where id=%s", (db["bb"],))
        c.commit()
    with psycopg.connect(PG) as c:
        assert c.execute("select name from brands where id=%s", (db["bb"],)).fetchone()[0] == "B brand"


def test_users_cannot_write_plans_directly(db):
    with as_user(db["a"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into plans(brand_id,user_id,status) values (%s,%s,'done')", (db["ba"], db["a"]))


def test_plan_must_use_own_brand_even_for_server(db):
    with psycopg.connect(PG) as c:
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("insert into plans(brand_id,user_id) values (%s,%s)", (db["bb"], db["a"]))


def test_anonymous_sees_nothing(db):
    with psycopg.connect(PG) as c:
        c.execute("set role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select * from brands")


def test_storage_policy_owner_folder_only(db):
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute("insert into storage.objects(bucket_id,name) values ('fasla-files',%s),('fasla-files',%s)",
                  (f"{db['a']}/x/y/input.xlsx", f"{db['b']}/x/y/input.xlsx"))
    with as_user(db["a"]) as c:
        rows = [r[0] for r in c.execute("select name from storage.objects")]
        assert rows == [f"{db['a']}/x/y/input.xlsx"]


def test_deleting_user_removes_their_data(db):
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute("delete from auth.users where id=%s", (db["b"],))
        assert c.execute("select count(*) from brands where user_id=%s", (db["b"],)).fetchone()[0] == 0
        assert c.execute("select count(*) from plans where user_id=%s", (db["b"],)).fetchone()[0] == 0
