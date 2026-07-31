-- Deleting a CE previously cascaded to delete their ce_assignments too,
-- silently wiping call history. Switch to RESTRICT so a CE with any
-- assignments (pending or called) can't be hard-deleted — the app should
-- reassign their pending queue to another CE first, then deactivate
-- (is_active=false) rather than delete, to keep call history intact.

ALTER TABLE ce_assignments DROP CONSTRAINT IF EXISTS ce_assignments_ce_id_fkey;
ALTER TABLE ce_assignments
    ADD CONSTRAINT ce_assignments_ce_id_fkey
    FOREIGN KEY (ce_id) REFERENCES customer_executives(id) ON DELETE RESTRICT;
