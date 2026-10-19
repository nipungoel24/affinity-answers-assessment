-- Q2 - Rfam public MySQL database
--   mysql --user rfamro --host mysql-rfam-public.ebi.ac.uk --port 4497 --database Rfam
--
-- Each block starts with "-- name:" / "-- title:" headers; run_queries.py parses these
-- blocks, so this file is the single source of truth for the SQL that is executed.


-- =====================================================================================
-- Q2.1  How many types of Acacia plants can be found in the taxonomy table?
-- =====================================================================================
-- MySQL's default collation is case-insensitive, so '%Acacia%' also matches names such
-- as 'Kladothrips acaciae' (an insect), 'Millerozyma acaciae' (a yeast) and
-- 'Arracacia' (a different plant genus). Three readings are reported side by side.

-- name: q2_1a_acacia_genus
-- title: Q2.1 (a) Species in the genus Acacia - species LIKE 'Acacia %'
SELECT COUNT(*) AS acacia_types
FROM taxonomy
WHERE species LIKE 'Acacia %';

-- name: q2_1b_acacia_plants
-- title: Q2.1 (b) Plants (Viridiplantae) whose species or lineage mentions Acacia
SELECT COUNT(*) AS acacia_plant_types
FROM taxonomy
WHERE tax_string LIKE '%Viridiplantae%'
  AND (species LIKE '%Acacia%' OR tax_string LIKE '%Acacia%');

-- name: q2_1c_acacia_wildcard
-- title: Q2.1 (c) Any species name containing 'Acacia' (broad wildcard)
SELECT COUNT(*) AS acacia_wildcard_matches
FROM taxonomy
WHERE species LIKE '%Acacia%';


-- =====================================================================================
-- Q2.2  Which type of wheat has the longest DNA sequence?
-- =====================================================================================
-- Wheat is the genus Triticum; Rfam species names look like 'Triticum aestivum (bread wheat)'.

-- name: q2_2_longest_wheat
-- title: Q2.2 Wheat (Triticum) sequences ranked by length - top 5
SELECT tx.species,
       tx.ncbi_id,
       rf.rfamseq_acc,
       rf.length
FROM rfamseq rf
JOIN taxonomy tx ON rf.ncbi_id = tx.ncbi_id
WHERE tx.species LIKE 'Triticum %'
ORDER BY rf.length DESC
LIMIT 5;


-- =====================================================================================
-- Q2.3 / Q2.4  Families whose longest DNA sequence is > 1,000,000, longest first,
--              page 9 at 15 rows per page  ->  OFFSET (9 - 1) * 15 = 120.
-- =====================================================================================
-- Performance: rfamseq has ~81M rows and no index on length; full_region has ~10M rows.
-- The literal join order (family -> full_region -> rfamseq) does ~10M primary-key
-- lookups and did not finish in 10+ minutes on the public server. Instead:
--   * WHERE rf.length > 1000000 is result-identical to relying on HAVING alone:
--       a family has MAX(length) > 1e6  <=>  it has at least one row with length > 1e6,
--       and for such a family MAX over the filtered rows == MAX over all rows.
--   * STRAIGHT_JOIN (MySQL hint) forces rfamseq to be read first: one scan keeps the
--     ~160k long sequences, then indexed lookups into full_region (~3 min total).
-- HAVING is kept to state the requirement explicitly.
-- Many families tie on max length (e.g. 836,514,780 bp, a Triticum durum chromosome), so
-- rfam_acc is a tie-breaker; without it, which rows land on page 9 is not deterministic.

-- name: q2_3_page9_significant
-- title: Q2.3/Q2.4 Page 9 (15/page) - significant hits only (fr.is_significant = 1, Rfam standard)
SELECT STRAIGHT_JOIN
       f.rfam_acc,
       f.rfam_id,
       f.description,
       MAX(rf.length) AS max_sequence_length
FROM rfamseq rf
JOIN full_region fr ON fr.rfamseq_acc = rf.rfamseq_acc
JOIN family f ON f.rfam_acc = fr.rfam_acc
WHERE rf.length > 1000000
  AND fr.is_significant = 1
GROUP BY f.rfam_acc, f.rfam_id, f.description
HAVING MAX(rf.length) > 1000000
ORDER BY max_sequence_length DESC, f.rfam_acc
LIMIT 15 OFFSET 120;

-- name: q2_3_page9_all_regions
-- title: Q2.3/Q2.4 Page 9 (15/page) - all regions (no is_significant filter)
SELECT STRAIGHT_JOIN
       f.rfam_acc,
       f.rfam_id,
       f.description,
       MAX(rf.length) AS max_sequence_length
FROM rfamseq rf
JOIN full_region fr ON fr.rfamseq_acc = rf.rfamseq_acc
JOIN family f ON f.rfam_acc = fr.rfam_acc
WHERE rf.length > 1000000
GROUP BY f.rfam_acc, f.rfam_id, f.description
HAVING MAX(rf.length) > 1000000
ORDER BY max_sequence_length DESC, f.rfam_acc
LIMIT 15 OFFSET 120;
