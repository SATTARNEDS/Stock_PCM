import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'factory_stock.db')

WITHDRAWAL_ACTION_FILTER = """
(
    l.action = 'Withdrawn'
    OR l.action = 'withdraw'
    OR l.action = 'ขอเบิกยา'
    OR l.action = 'ขอเบิกอุปกรณ์'
    OR l.action LIKE 'เบิกหมวกเซฟตี้%'
)
"""


def connect_readonly(db_path):
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def connect_readwrite(db_path):
    conn = sqlite3.connect(os.path.abspath(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def create_backup(db_path, suffix='pre_cleanup'):
    source_path = os.path.abspath(db_path)
    timestamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    base, ext = os.path.splitext(source_path)
    backup_path = f"{base}.{suffix}_{timestamp}{ext or '.db'}"
    shutil.copy2(source_path, backup_path)
    return backup_path


def get_product_issues(conn, focus='', only_issues=True, limit=100):
    params = []
    focus_filter = ''
    if focus:
        focus_filter = "WHERE p.name LIKE ? OR p.code LIKE ?"
        focus_term = f"%{focus}%"
        params.extend([focus_term, focus_term])

    having = """
    HAVING
        lot_qty != stock
        OR approved_qty != withdraw
        OR approved_without_stock_audit_count > 0
        OR reserved_stock < 0
    """ if only_issues else ''

    query = f"""
        SELECT
            p.id,
            p.code,
            p.name,
            COALESCE(p.stock, 0) AS stock,
            COALESCE(p.reserved_stock, 0) AS reserved_stock,
            COALESCE(p.withdraw, 0) AS withdraw,
            COALESCE(p.unit, '') AS unit,
            COALESCE((
                SELECT SUM(COALESCE(pl.qty, 0))
                FROM product_lots pl
                WHERE pl.product_id = p.id
            ), 0) AS lot_qty,
            COALESCE(SUM(CASE
                WHEN l.status = 'Approved' AND {WITHDRAWAL_ACTION_FILTER}
                THEN COALESCE(l.qty, 0)
                ELSE 0
            END), 0) AS approved_qty,
            COALESCE(SUM(CASE
                WHEN l.status = 'Approved' AND {WITHDRAWAL_ACTION_FILTER}
                THEN 1
                ELSE 0
            END), 0) AS approved_count,
            COALESCE(SUM(CASE
                WHEN l.status = 'Approved'
                     AND {WITHDRAWAL_ACTION_FILTER}
                     AND (l.note IS NULL OR l.note NOT LIKE '%ตัดสต็อก:%')
                THEN 1
                ELSE 0
            END), 0) AS approved_without_stock_audit_count
        FROM products p
        LEFT JOIN transaction_logs l ON l.product_id = p.id
        {focus_filter}
        GROUP BY p.id
        {having}
        ORDER BY approved_without_stock_audit_count DESC, ABS(lot_qty - stock) DESC, p.id ASC
        LIMIT ?
    """
    params.append(limit)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_product_detail(conn, product_id):
    product = conn.execute(
        """
        SELECT id, code, name, stock, reserved_stock, withdraw, unit, category
        FROM products
        WHERE id = ?
        """,
        (product_id,)
    ).fetchone()
    if not product:
        return None

    logs = conn.execute(
        """
        SELECT id, emp_id, action, qty, qty_base_unit, qty_package_unit,
               status, timestamp, note, lot_id
        FROM transaction_logs
        WHERE product_id = ?
        ORDER BY id ASC
        """,
        (product_id,)
    ).fetchall()
    lots = conn.execute(
        """
        SELECT id, lot_number, qty, received_date, expiry_date
        FROM product_lots
        WHERE product_id = ?
        ORDER BY received_date ASC, id ASC
        """,
        (product_id,)
    ).fetchall()

    return {
        'product': dict(product),
        'logs': [dict(row) for row in logs],
        'lots': [dict(row) for row in lots],
    }


def get_cleanup_plan(conn, focus='', limit=500, include_referenced_invalid=False):
    params = [1 if include_referenced_invalid else 0]
    focus_filter = ''
    if focus:
        focus_filter = "AND (p.name LIKE ? OR p.code LIKE ?)"
        focus_term = f"%{focus}%"
        params.extend([focus_term, focus_term])

    query = f"""
        WITH refs AS (
            SELECT lot_id, COUNT(*) AS ref_count
            FROM transaction_logs
            WHERE lot_id IS NOT NULL
            GROUP BY lot_id
        )
        SELECT
            pl.id AS lot_id,
            pl.product_id,
            p.code,
            p.name,
            COALESCE(p.stock, 0) AS product_stock,
            COALESCE(pl.lot_number, '') AS lot_number,
            COALESCE(pl.qty, 0) AS lot_qty,
            COALESCE(pl.received_date, '') AS received_date,
            COALESCE(refs.ref_count, 0) AS ref_count,
            CASE
                WHEN (
                        pl.lot_number LIKE '%.0'
                        OR pl.lot_number LIKE '%LOT%'
                        OR pl.lot_number GLOB '*[^0-9A-Za-z_-]*'
                     )
                     AND (
                        COALESCE(refs.ref_count, 0) = 0
                        OR ? = 1
                     )
                THEN 1
                ELSE 0
            END AS deletable
        FROM product_lots pl
        JOIN products p ON p.id = pl.product_id
        LEFT JOIN refs ON refs.lot_id = pl.id
        WHERE COALESCE(pl.qty, 0) > 0
          AND (
                pl.lot_number LIKE '%.0'
                OR pl.lot_number LIKE '%LOT%'
                OR pl.lot_number GLOB '*[^0-9A-Za-z_-]*'
          )
          {focus_filter}
        ORDER BY deletable DESC, pl.qty DESC, p.code ASC
        LIMIT ?
    """
    params.append(limit)
    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    deletable = [row for row in rows if row['deletable']]
    blocked = [row for row in rows if not row['deletable']]
    return {
        'summary': {
            'candidate_rows': len(rows),
            'deletable_rows': len(deletable),
            'blocked_rows': len(blocked),
            'deletable_qty_total': sum(int(row['lot_qty'] or 0) for row in deletable),
        },
        'delete_lot_ids': [row['lot_id'] for row in deletable],
        'deletable': deletable,
        'blocked': blocked,
    }


def get_mismatch_summary(conn):
    row = conn.execute(
        """
        WITH lot_stats AS (
            SELECT product_id, SUM(COALESCE(qty, 0)) AS lot_qty
            FROM product_lots
            GROUP BY product_id
        )
        SELECT
            COUNT(*) AS product_count,
            SUM(CASE WHEN COALESCE(lot_stats.lot_qty, 0) != COALESCE(products.stock, 0) THEN 1 ELSE 0 END) AS mismatch_count
        FROM products
        LEFT JOIN lot_stats ON lot_stats.product_id = products.id
        """
    ).fetchone()
    return dict(row)


def apply_cleanup(db_path, focus='', limit=500, include_referenced_invalid=False):
    backup_path = create_backup(db_path)
    conn = connect_readwrite(db_path)
    try:
        before_summary = get_mismatch_summary(conn)
        conn.execute('BEGIN IMMEDIATE')
        plan = get_cleanup_plan(
            conn,
            focus=focus,
            limit=limit,
            include_referenced_invalid=include_referenced_invalid,
        )
        delete_lot_ids = plan['delete_lot_ids']
        affected_product_ids = sorted({
            row['product_id']
            for row in plan['deletable']
            if row['lot_id'] in delete_lot_ids
        })

        detached_log_count = 0
        for lot in plan['deletable']:
            if not lot['ref_count']:
                continue
            cleanup_note = f"cleanup lot ผิดรูปแบบ: ลบ lot_id={lot['lot_id']} lot_number={lot['lot_number']}"
            cursor = conn.execute(
                """
                UPDATE transaction_logs
                SET
                    note = CASE
                        WHEN note IS NULL OR note = '' THEN ?
                        ELSE note || ' | ' || ?
                    END,
                    lot_id = NULL
                WHERE lot_id = ?
                """,
                (cleanup_note, cleanup_note, lot['lot_id']),
            )
            detached_log_count += cursor.rowcount

        if delete_lot_ids:
            placeholders = ','.join('?' for _ in delete_lot_ids)
            conn.execute(
                f"DELETE FROM product_lots WHERE id IN ({placeholders})",
                delete_lot_ids,
            )

        after_product_status = []
        for product_id in affected_product_ids:
            row = conn.execute(
                """
                SELECT
                    p.id,
                    p.code,
                    p.name,
                    COALESCE(p.stock, 0) AS product_stock,
                    COALESCE(SUM(pl.qty), 0) AS remaining_lot_qty
                FROM products p
                LEFT JOIN product_lots pl ON pl.product_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (product_id,),
            ).fetchone()
            if row:
                after_product_status.append(dict(row))

        conn.commit()
        after_summary = get_mismatch_summary(conn)
        return {
            'applied': True,
            'backup_path': backup_path,
            'deleted_count': len(delete_lot_ids),
            'detached_log_count': detached_log_count,
            'deleted_lot_ids': delete_lot_ids,
            'affected_product_ids': affected_product_ids,
            'before_summary': before_summary,
            'after_summary': after_summary,
            'after_product_status': after_product_status,
            'note': 'Product stock was not adjusted by this script. Referenced invalid lots are detached from logs only when --include-referenced-invalid is used.',
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Stock mismatch report and guarded cleanup for PCM.')
    parser.add_argument('--db', default=DB_PATH, help='Path to SQLite database.')
    parser.add_argument('--focus', default='', help='Filter by product name/code, such as Tape or STAT-CC-076.')
    parser.add_argument('--all', action='store_true', help='Show products without detected issues too.')
    parser.add_argument('--limit', type=int, default=100, help='Maximum products to show.')
    parser.add_argument('--product-id', type=int, help='Show detailed logs/lots for one product.')
    parser.add_argument('--cleanup-plan', action='store_true', help='Show weird lots that are safe to delete because ref_count is 0.')
    parser.add_argument('--cleanup-apply', action='store_true', help='Delete weird lots with ref_count = 0 after creating a backup.')
    parser.add_argument('--include-referenced-invalid', action='store_true', help='Also delete invalid lot numbers that are referenced by logs after detaching lot_id and adding a log note.')
    parser.add_argument('--confirm-delete-weird-lots', action='store_true', help='Required with --cleanup-apply to prevent accidental deletion.')
    parser.add_argument('--summary', action='store_true', help='Show product/stock mismatch summary.')
    args = parser.parse_args()

    if args.cleanup_apply:
        if not args.confirm_delete_weird_lots:
            payload = {
                'applied': False,
                'error': 'Missing --confirm-delete-weird-lots. No data was changed.',
                'next_step': 'Run --cleanup-plan first, then rerun with --cleanup-apply --confirm-delete-weird-lots if the plan is correct.',
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        payload = apply_cleanup(
            args.db,
            focus=args.focus.strip(),
            limit=max(1, args.limit),
            include_referenced_invalid=args.include_referenced_invalid,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    conn = connect_readonly(args.db)
    try:
        if args.summary:
            payload = get_mismatch_summary(conn)
        elif args.cleanup_plan:
            payload = get_cleanup_plan(
                conn,
                focus=args.focus.strip(),
                limit=max(1, args.limit),
                include_referenced_invalid=args.include_referenced_invalid,
            )
        elif args.product_id:
            payload = get_product_detail(conn, args.product_id)
        else:
            payload = get_product_issues(
                conn,
                focus=args.focus.strip(),
                only_issues=not args.all,
                limit=max(1, args.limit),
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
