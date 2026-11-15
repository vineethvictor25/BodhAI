"""Explicit offline SQLite column encryption/decryption; never runs during HTTP startup."""
import json
import base64
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .pii import PREFIX,field_aad


def migrate(filename,source,policy,*,decrypt=False,backup=None):
    if not policy.cipher.key:
        raise ValueError("Initialize and configure the encryption key first")
    path=Path(filename).resolve()
    if not path.is_file():
        raise ValueError("The source SQLite file must already exist")
    backup_path=Path(backup or str(path)+".pii-backup.enc").resolve()
    if backup_path==path or backup_path.exists():
        raise ValueError("Choose a new encrypted backup path, separate from the source database")
    mapping=policy.columns.get(source)
    if not mapping:
        raise ValueError("Configure PII columns for this source first")
    quote=lambda value:'"'+value.replace('"','""')+'"'
    changed=0
    with closing(sqlite3.connect(path)) as conn:
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower()!="delete":
            raise ValueError("Offline column migration currently requires SQLite DELETE journal mode. Stop the app and review journal settings first.")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("BEGIN EXCLUSIVE")
        try:
            plans=[]
            for table,columns in mapping.items():
                info=conn.execute("PRAGMA table_info("+quote(table)+")").fetchall()
                if not info:
                    raise ValueError("Configured PII table does not exist")
                by_name={column[1]:column for column in info}
                keys=[column[1] for column in sorted(info,key=lambda c:c[5]) if column[5]]
                foreign={row[3] for row in conn.execute("PRAGMA foreign_key_list("+quote(table)+")")}
                indexed=set()
                for index in conn.execute("PRAGMA index_list("+quote(table)+")"):
                    if index[2]:
                        indexed.update(row[2] for row in conn.execute("PRAGMA index_info("+quote(index[1])+")"))
                if not keys or any(column not in by_name or column in keys or column in foreign or column in indexed or
                        "TEXT" not in by_name[column][2].upper() for column in columns):
                    raise ValueError("Local PII encryption requires TEXT columns outside primary/foreign/unique keys and a stable table primary key")
                plans.append((table,columns,keys))
            # Serialize a consistent in-memory snapshot and encrypt the backup before any data mutation.
            snapshot=conn.serialize()
            envelope=policy.cipher.encrypt({"database":base64.b64encode(snapshot).decode()},'sqlite-backup:'+source)
            backup_path.parent.mkdir(parents=True,exist_ok=True)
            with backup_path.open("x",encoding="utf-8") as output:
                output.write(envelope);output.flush();os.fsync(output.fileno())
            for table,columns,keys in plans:
                selected=keys+columns
                rows=conn.execute("SELECT "+",".join(map(quote,selected))+" FROM "+quote(table)).fetchall()
                for row in rows:
                    identity=list(row[:len(keys)])
                    for index,column in enumerate(columns):
                        value=row[len(keys)+index]
                        if value is None:
                            continue
                        aad=field_aad(source,table,column,identity)
                        if decrypt:
                            converted=policy.cipher.decrypt(value,aad)
                        elif isinstance(value,str) and value.startswith(PREFIX):
                            policy.cipher.decrypt(value,aad) # verify existing ciphertext, do not double-encrypt
                            continue
                        else:
                            converted=policy.cipher.encrypt(value,aad)
                        if converted!=value:
                            conn.execute("UPDATE "+quote(table)+" SET "+quote(column)+"=? WHERE "+" AND ".join(quote(key)+"=?" for key in keys),[converted]+identity)
                            changed+=1
            conn.commit()
            conn.execute("VACUUM")
        except Exception:
            conn.rollback()
            raise
    return {"changed_values":changed,"encrypted_backup":str(backup_path)}
