function calculateCosts() {
    const quantity = parseFloat(document.getElementById('quantity').value);
    const costPerUnit = parseFloat(document.getElementById('costPerUnit').value);
    if (isNaN(quantity) || isNaN(costPerUnit)) {
      alert('Пожалуйста, введите корректные значения.');
      return false;
    }
    const total = quantity * costPerUnit;
    document.getElementById('totalCost').textContent = 'Общая стоимость: ' + total.toFixed(2) + ' руб.';
    return false; // Предотвратить отправку формы
  }