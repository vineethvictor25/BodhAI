SELECT TOP 10 sku, ISNULL(price, 0) AS price, GETDATE() AS loaded_at
FROM dbo.products
WHERE LEN(sku) > 0;
