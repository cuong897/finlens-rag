"""Consistent SQLite + referenced-original backups; never include credentials."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile


class BackupError(ValueError):
    pass


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def member(root, name):
    if not isinstance(name,str) or '\\' in name or name.startswith('/') or ':' in name or '..' in Path(name).parts:
        raise BackupError('Đường dẫn trong bản sao không hợp lệ.')
    path=root/name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise BackupError('Tệp nguồn không nằm trong phạm vi bản sao.')
    return path


def read_db(path):
    return closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True))


def references(db):
    return db.execute('''SELECT d.pdf_path,COALESCE(t.file_sha256,d.sha256)
        FROM documents d LEFT JOIN table_sources t ON t.document_id=d.id
        WHERE d.pdf_path IS NOT NULL''').fetchall()


def check_db(path):
    with read_db(path) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise BackupError('Bản sao cơ sở dữ liệu không toàn vẹn.')


def verify_backup(directory):
    root=Path(directory).resolve()
    try:
        manifest_path=root/'manifest.json'
        if manifest_path.stat().st_size>4*1024**2:raise BackupError('Manifest quá lớn.')
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(manifest,dict) or type(manifest.get('version')) is not int or manifest['version']!=1 or not isinstance(manifest.get('files'),dict):
            raise BackupError('Định dạng bản sao không được hỗ trợ.')
        entries=manifest['files']
        if 'finlens.sqlite3' not in entries:raise BackupError('Bản sao thiếu cơ sở dữ liệu.')
        for name,entry in entries.items():
            if name!='finlens.sqlite3' and not name.startswith('uploads/'):
                raise BackupError('Bản sao chứa đường dẫn không được phép.')
            path=member(root,name)
            if not path.is_file() or path.stat().st_size!=entry['bytes'] or digest(path)!=entry['sha256']:
                raise BackupError('Checksum bản sao không khớp; dừng khôi phục.')
        check_db(root/'finlens.sqlite3')
        with read_db(root/'finlens.sqlite3') as db:
            for name,expected in references(db):
                if name not in entries or entries[name]['sha256']!=expected:
                    raise BackupError('Bản sao thiếu tệp gốc khớp tài liệu.')
        return manifest
    except (OSError,sqlite3.Error,KeyError,TypeError,json.JSONDecodeError) as exc:
        raise BackupError('Không đọc được bản sao hoặc manifest không hợp lệ.') from exc


def destination_path(source,destination):
    source,target=Path(source).resolve(),Path(destination).resolve()
    if target.exists() or source==target or source in target.parents or target in source.parents:
        raise BackupError('Đích phải là thư mục mới, tách khỏi thư mục nguồn.')
    target.parent.mkdir(parents=True,exist_ok=True)
    return source,target


def create_backup(data_dir,destination):
    source,target=destination_path(data_dir,destination)
    if not (source/'finlens.sqlite3').is_file():raise BackupError('Không tìm thấy kho dữ liệu nguồn.')
    with tempfile.TemporaryDirectory(prefix='.'+target.name+'.backup-',dir=target.parent) as work:
        stage=Path(work).resolve()
        if stage.parent!=target.parent:raise BackupError('Thư mục tạm không hợp lệ.')
        with read_db(source/'finlens.sqlite3') as original:
            with closing(sqlite3.connect(stage/'finlens.sqlite3')) as copy:
                original.backup(copy)
                copy.execute('PRAGMA journal_mode=DELETE')
        files={}
        with read_db(stage/'finlens.sqlite3') as db:
            for name,expected in references(db):
                if not name.startswith('uploads/'):raise BackupError('Tệp gốc nằm ngoài thư mục uploads.')
                src,dst=member(source,name),member(stage,name)
                dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
                actual=digest(dst)
                if actual!=expected:raise BackupError('Tệp gốc không khớp checksum tài liệu.')
                files[name]={'sha256':actual,'bytes':dst.stat().st_size}
        path=stage/'finlens.sqlite3';files['finlens.sqlite3']={'sha256':digest(path),'bytes':path.stat().st_size}
        manifest={'version':1,'created_at':datetime.now(timezone.utc).isoformat(),'files':files,
                  'contents':'SQLite, embeddings and referenced original files; excludes env, keys, model runtimes and OCR downloads.'}
        (stage/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        verify_backup(stage)
        stage.rename(target)
    return {'destination':str(target),'files':len(files),'verified':True}


def restore_backup(backup_dir,destination):
    source,target=destination_path(backup_dir,destination)
    manifest=verify_backup(source)
    with tempfile.TemporaryDirectory(prefix='.'+target.name+'.restore-',dir=target.parent) as work:
        stage=Path(work).resolve()
        if stage.parent!=target.parent:raise BackupError('Thư mục tạm không hợp lệ.')
        for name in manifest['files']:
            src,dst=member(source,name),member(stage,name)
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        shutil.copyfile(source/'manifest.json',stage/'manifest.json')
        verify_backup(stage)
        # Restore only data files; the backup manifest is not live application data.
        (stage/'manifest.json').unlink()
        stage.rename(target)
    return {'destination':str(target),'files':len(manifest['files']),'verified':True}
