select 
-- presu.idpresudet,
max(presu.fechafacturacion) as fechafacturacion,  --0
max(presu.nropresupuesto) as nropresupuesto, --1
coalesce(max(otra.ordennro), '-') as ordennro, --2
coalesce(otra.estado,'-') as estado, --3
max(presu.cliente) as cliente , --4
max(presu.condicion) as condicion , --5
-- max(presu.idprodu) as idprodu ,
--nueva columna
max(presu.tipo_producto) as tipo_producto,
max(presu.codigo) as codigo , --6

-- separar
concat(' ', '|') as sep1,

case when (max(otra.esplancha)= 'SI') then 'SI' else '-' end as esplancha, --7
case when (max(otra.tienecorte)= 'SI') then 'SI' else '-' end  as tienecorte, --8
coalesce(max(otra.nCorte), 0) as nCorte, --9
coalesce(max(presu.m2), 0.0) as m2 , --10
coalesce(max(presu.preciom2),0.0) as preciom2 , --12
coalesce(max(presu.precioplancha), 0.0) as precioplancha, --13
max(presu.listaPrecio) as listaPrecio, --14
max(presu.preciolistam2) as preciolistam2, --15
max(presu.preciolistaPla) as preciolistaPla, --16

-- separar
concat(' ', '|') as sep2,

max(presu.totalusd) as totalusd , --17
max(presu.preciototalgs) as preciototalgs , --18
coalesce(max(presu.descuento), '0.0') as descuento, --19
-- case when (max(presu.descuento) = '0.0') 
-- 	then '-'
-- 	else max(presu.descuento)
-- end as descuento ,
max(presu.espera) as espera , --20

--- separar
concat(' ', '|') as sep3,
-- anexo
coalesce(max(presu.tipo_anexo),'-') as tipo_anexo,
max(presu.cantidad_anexo) as cantidad_anexo,
max(presu.precio_unitario_anexo) as precio_unitario_anexo,

--- separar
concat(' ', '|') as sep4,
max(presu.vendedor) as vendedor , --21
presu.nrofactura as  nrofactura, --22
case when ('SI' = '##ver-ganan##') 
	then max(presu.porgananciasUSD)
	else 0.0 
end as porgananciasUSD , --23
case when ('SI' = '##ver-ganan##') 
	then max(presu.gananciaUsd)
	else 0.0 
end as gananciaUsd , --24
case when ('SI' = '##ver-ganan##') 
	then max(presu.costoUsd)
	else 0.0 
end as costoUsd , --25
case when ('SI' = '##ver-ganan##') 
	then max(presu.gananciaGs)
	else 0.0 
end as gananciaGs , --26
case when ('SI' = '##ver-ganan##') 
	then max(presu.costoGs)
	else 0.0 
end as costoGs , --27
case when ('SI' = '##ver-ganan##') 
	then max(presu.utilidad)
	else 0.0 
end as utilidad , --28
----------------------------------------------------
--==================================================
max(presu.cambio) as cambio , --29
max(presu.fechaFacDia) as fechaFacDia , --30 
max(presu.fechaFacSemana) as fechaFacSemana, --31
max(presu.fechaFacMes) as fechaFacMes , --32
max(presu.fechaFacAnio) as fechaFacAnio, --33
coalesce(max(otra.idop), 0) as idop --34
from
(
 -------------------------------------------------------------------------------------------------------------
 -------------------------------------------------------------------------------------------------------------
select 
concat(pro.id,'|',predet.cantidad,'|', predet.corte) as clave,
predet.id as idpresudet, 
to_char(pre.fechafacturacion, 'yyyy-mm-dd') as fechafacturacion,
---cab.fechafacturacion as fecha,
pre.nropresupuesto,
pre.nrofactura,
concat(emp.razonsocial, ' (', emp.nombrefantasia,')') as cliente,
case when (pre.idcondicionpago = 58) then 'Contado' else 'Crédito' end condicion,
pre.vendedor,
case when (pre.espera = true) then 'SI' else '-' end espera,
case when predet.corte = true then 'SI' else '-' end as corte,
pro.id as idprodu,

case 
 	when pro.tipo_producto = 'ANEXO'  then
-- 		case when  (LENGTH(pro.codigo) < 4) then pro.descripcion else pro.codigo end
 		concat(pro.codigo, '-', pro.descripcion)
 	else pro.codigo
end as codigo,

-- pro.codigo,

(predet.metros2 * predet.cantidad) as m2,
predet.preciom2,
case when predet.corte = true 
	then 0.0
	else predet.precioplancha
end precioplancha, 
case when predet.corte = true 
	then predet.preciooriginal
	else 0.0
end as preciolistam2,
case when predet.corte = false 
	then predet.preciooriginal
	else 0.0
end as preciolistaPla,
-- predet.preciooriginal as preciolista,
predet.preciototal as totalusd,
predet.preciototalgs,
case when (predet.preciooriginal = 0) 
	then 'ND'
	else
		to_char(
			case when predet.corte = true 
				then  (1 - (predet.preciom2      / predet.preciooriginal)) * 100
				else  (1 - (predet.precioplancha / predet.preciooriginal)) * 100
			end , '990.0')
	end	 as descuento,
case
	when pro.tipo_producto = 'ANEXO' then predet.cantidad
	else 0.0
end as cantidad_anexo,
case
	when pro.tipo_producto = 'ANEXO' then predet.preciounitario
	else 0.0
end as precio_unitario_anexo,
pre.cotizaciondeldiags as cambio,
predet.porcentajeganancia as porgananciasUSD,
predet.gsganancia as gananciaUsd,
(predet.preciototal - predet.gsganancia) as costoUsd,
(predet.gsganancia * pre.cotizaciondeldiags) as gananciaGs,
(predet.preciototalgs - (predet.gsganancia * pre.cotizaciondeldiags)) as costoGs,
-- ((predet.gsganancia / predet.preciototal)*100) as utilidad,
 case when predet.preciototal <> 0.0 then  ((predet.gsganancia / predet.preciototal)*100) else 0.0 end as utilidad,
---------------------------
to_char(pre.fechafacturacion, 'dd') as fechaFacDia, 
to_char(pre.fechafacturacion, 'IW') as fechaFacSemana,
to_char(pre.fechafacturacion, 'mm') as fechaFacMes,
to_char(pre.fechafacturacion, 'yyyy') as fechaFacAnio,
pre.id as idpresu,
--coalesce(pla.nombre, 'zERROR-LISTA') as listaPrecio,
case 
	when predet.idplanilla is not null then pla.nombre
	when predet.idplanillaanexo is not null then plaane.nombre
	else 'ERROR-LISTA'
end as listaPrecio,
tipoanexo.descripcion as tipo_anexo,
pro.tipo_producto as tipo_producto
from presupuestodetalle predet
left join presupuesto pre on pre.id = predet.idpresupuesto
left join producto pro on pro.id = predet.idproducto
left join tipo tipovidrio on tipovidrio.id = pro.idtipoproductovidrio
left join cliente cli on cli.id = pre.idcliente
left join empresa emp on emp.id = cli.idempresa
left join planilla pla on pla.id = predet.idplanilla
left join planillaanexo plaane on plaane.id = predet.idplanillaanexo
left join tipo tipoanexo on tipoanexo.id = pro.idtipoproductoanexo
where 
	--	pro.tipo_producto = 'VIDRIO' ---  VIDRIO
	-- and predet.idproducto <> 687	 ---  VIDRIO DEL CLIENTE
	predet.idproducto <> 687	 ---  VIDRIO DEL CLIENTE
	and pre.estadopresupuesto <> 2   ---  facturado
	and	pre.nrofactura is not null   ---  facturado
	--- incluir SOLO presupuestos que tengan al menos una fila con DVH en codigo
	--- (se filtra por presupuesto completo, no por fila individual)
	and exists (
		select 1
		from presupuestodetalle predetdvh
		left join producto prodvh on prodvh.id = predetdvh.idproducto
		where predetdvh.idpresupuesto = pre.id
		  and case 
				when prodvh.tipo_producto = 'ANEXO' 
					then concat(prodvh.codigo, '-', prodvh.descripcion) 
				else prodvh.codigo 
			  end ilike '%DVH%'
	)
 order by pre.fechafacturacion, pro.codigo
 -------------------------------------------------------------------------------------------------------------
 ------------------------------------------------------------------------------------------------------------- 
 ) as presu
left join
 (
 -------------------------------------------------------------------------------------------------------------
 -------------------------------------------------------------------------------------------------------------
 -------------------------------------------------------------------------------------------------------------
select 
concat(pro.id,'|',opd.cantidadproductocabecera,'|', not(gg.esplancha)) as clave,
pre.id as idpresu, 
pre.nropresupuesto,
op.ordennro,
tp.descripcion as estado,
op.id as idop,
case when (gg.esplancha = true) then 'SI' else 'NO' end as esplancha,
case when (gg.tienecorte = true) then 'SI' else 'NO' end as tienecorte,
gg.nCorte as nCorte, 
opd.idproductocabecera as iproducto,
pro.codigo,
--opd.nombreproducto,
case when (gg.esplancha = true) then opd.cantidadproductocabecera else 0 end as nnPlanchas,
case when (gg.esplancha = true) then (pro.ancho * pro.alto) else 0 end as n2Planchas,
case when (gg.esplancha = false) then opd.cantidadproductocabecera else 0 end as nnCortes,
case when (gg.esplancha = false) then (opd.ancho * opd.alto) else 0 end as n2Corte, -- ,
case when (gg.esplancha = true) 
		then (opd.cantidadproductocabecera * pro.ancho * pro.alto) 
		else (opd.cantidadproductocabecera * opd.ancho * opd.alto) 
	end as n2Total 
from ordendepedidodetalle opd 
left join (
----------------------------------------------
----------------------------------------------
select 
opd.agrupador,
((max(opd.tipo)= 'PLA')or(max(opd.tipo)= 'MED')) as esPlancha,
count(*) as nCorte,
(count(*) > 1) as tieneCorte
from ordendepedidodetalle opd 
where 
-- opd.idordendepedidodetalle = 50405 and  --50396 and  --50405 and
-- opd.idordendepedidodetalle > 1000 and 
---opd.creado > '2021-09-20'
-- op.idpresupuesto = 101184
-- and 
opd.idordendepedidodetalle is not null
group by --opd.tipo, 
-- opd.idordendepedidodetalle, 
opd.agrupador
----------------------------------------------
----------------------------------------------
) as gg on gg.agrupador = opd.agrupador 	 
left join producto pro on pro.id = opd.idproductocabecera
left join ordendepedido op on op.id = opd.idordendepedidodetalle
	 -- nueva columna
left join tipo tp on tp.id = op.idtipoestado
left join presupuesto pre on pre.id = op.idpresupuesto
where 
--opd.idordendepedidodetalle = 50405 and
(
      (gg.esPlancha = true  and opd.escabecera = true  )
   or (gg.esPlancha = false and opd.escabecera = false )

  )
-- and pre.id = 101408 
 -------------------------------------------------------------------------------------------------------------
 -------------------------------------------------------------------------------------------------------------
 -------------------------------------------------------------------------------------------------------------
 ) as otra
on presu.idpresu = otra.idpresu and presu.clave = otra.clave
where  

-- presu.idpresu = 101408 
 presu.fechafacturacion >= '2026-03-01 00:00:00' and  presu.fechafacturacion <= '2026-03-31 23:59:59'

group by  presu.idpresudet, presu.nrofactura, otra.estado
order by presu.nrofactura, presu.idpresudet