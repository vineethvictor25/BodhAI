-- Reference DDL; SQLite runtime stores governed JSON rows. Review before cloud deployment.
CREATE TABLE products (
  product_id VARCHAR NOT NULL,
  product_name VARCHAR NOT NULL,
  length_m DOUBLE,
  price DOUBLE NOT NULL,
  currency VARCHAR,
  category VARCHAR,
  PRIMARY KEY (product_id)
);
